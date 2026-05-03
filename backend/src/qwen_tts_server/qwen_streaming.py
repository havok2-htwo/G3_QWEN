from __future__ import annotations

import concurrent.futures
from typing import Any

import numpy as np
import torch
from qwen_tts import Qwen3TTSModel
from torch.nn.utils.rnn import pad_sequence


class Qwen3TTSStreamingModel(Qwen3TTSModel):
    """Qwen3 TTS wrapper with incremental talker/codebook decoding for Base models."""

    @torch.inference_mode()
    def generate_batch_voice_clone_stream(
        self,
        *,
        texts: list[str],
        language: str = 'Auto',
        voice_clone_prompt: dict[str, Any] | None = None,
        chunk_size: int = 20,
        overlap: int = 4,
        top_k: int = 50,
        top_p: float = 1.0,
        temperature: float = 0.9,
        max_new_tokens: int = 2048,
    ):
        if voice_clone_prompt is None:
            raise ValueError('voice_clone_prompt is required for native Base streaming.')
        if not texts:
            return

        batch_size = len(texts)
        device = self.device
        chunk_size = max(2, int(chunk_size))
        overlap = max(0, min(int(overlap), chunk_size - 1))

        decode_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pending_decodes: list[concurrent.futures.Future] = []

        tts_bos_id = self.model.config.tts_bos_token_id
        tts_eos_id = self.model.config.tts_eos_token_id
        tts_pad_id = self.model.config.tts_pad_token_id

        def text_proj(ids):
            return self.model.talker.text_projection(self.model.talker.get_text_embeddings()(ids))

        special_ids = torch.tensor([[tts_bos_id, tts_eos_id, tts_pad_id]], device=device)
        special_embeds = text_proj(special_ids)
        tts_bos_embed, tts_eos_embed, tts_pad_embed_single = special_embeds.chunk(3, dim=1)

        ref_spk = voice_clone_prompt['ref_spk_embedding'][0].to(device).view(1, 1, -1)

        c_conf = self.model.config.talker_config
        if language.lower() == 'auto':
            lang_id = c_conf.codec_language_id.get('english')
        else:
            lang_id = c_conf.codec_language_id.get(language.lower(), c_conf.codec_language_id.get('english'))

        codec_prefill = torch.tensor(
            [[c_conf.codec_think_id, c_conf.codec_think_bos_id, lang_id, c_conf.codec_think_eos_id]],
            device=device,
        )
        codec_embed_0 = self.model.talker.get_input_embeddings()(codec_prefill)
        codec_suffix = torch.tensor([[c_conf.codec_pad_id, c_conf.codec_bos_id]], device=device)
        codec_embed_1 = self.model.talker.get_input_embeddings()(codec_suffix)
        codec_input_block = torch.cat([codec_embed_0, ref_spk, codec_embed_1], dim=1)

        processed_inputs_list = []
        trailing_hidden_list = []

        for text in texts:
            processed_text = self._build_assistant_text(text)
            t_inputs = self.processor(text=processed_text, return_tensors='pt', padding=False)
            input_ids_single = t_inputs['input_ids'].to(device)

            header_ids = input_ids_single[:, :3]
            first_id = input_ids_single[:, 3:4]
            trailing_ids = input_ids_single[:, 4:-5]

            header_embed = text_proj(header_ids)
            pad_filler = tts_pad_embed_single[:, :1, :].expand(-1, codec_input_block.shape[1] - 2, -1)
            middle_part = torch.cat((pad_filler, tts_bos_embed), dim=1) + codec_input_block[:, :-1]
            talker_input_part1 = torch.cat((header_embed, middle_part), dim=1)
            first_text_token = text_proj(first_id)
            codec_bos_part = codec_input_block[:, -1:]

            full_input_embed = torch.cat([talker_input_part1, first_text_token + codec_bos_part], dim=1)
            processed_inputs_list.append(full_input_embed.squeeze(0))

            trailing_emb = text_proj(trailing_ids)
            trailing_emb = torch.cat((trailing_emb, tts_eos_embed), dim=1)
            trailing_hidden_list.append(trailing_emb.squeeze(0))

        def pad_left(tensor_list, pad_vec):
            max_len = max(t.shape[0] for t in tensor_list)
            final_batch = []
            if pad_vec.dim() == 3:
                pad_vec = pad_vec.squeeze(0)
            if pad_vec.dim() == 2:
                pad_vec = pad_vec.squeeze(0)
            for tensor in tensor_list:
                diff = max_len - tensor.shape[0]
                if diff > 0:
                    pads = pad_vec.unsqueeze(0).expand(diff, -1)
                    final_batch.append(torch.cat([pads, tensor], dim=0))
                else:
                    final_batch.append(tensor)
            return torch.stack(final_batch)

        initial_input = pad_left(processed_inputs_list, tts_pad_embed_single)
        trailing_lens = [t.shape[0] for t in trailing_hidden_list]
        max_trail = max(trailing_lens)
        trailing_embeds = torch.zeros(
            batch_size,
            max_trail,
            trailing_hidden_list[0].shape[1],
            device=device,
            dtype=initial_input.dtype,
        )
        for index, trailing in enumerate(trailing_hidden_list):
            trailing_embeds[index, : trailing.shape[0], :] = trailing

        tts_pad_embed_batch = tts_pad_embed_single.expand(batch_size, -1, -1)

        if hasattr(torch, 'compiler') and hasattr(torch.compiler, 'cudagraph_mark_step_begin'):
            torch.compiler.cudagraph_mark_step_begin()
        outputs = self.model.talker(
            inputs_embeds=initial_input,
            past_key_values=None,
            use_cache=True,
            trailing_text_hidden=trailing_embeds,
            tts_pad_embed=tts_pad_embed_batch,
            generation_step=-1,
        )

        past_key_values = outputs.past_key_values
        past_hidden = outputs.past_hidden
        next_token_logits = outputs.logits[:, -1, :]
        if temperature > 0:
            probs = torch.softmax(next_token_logits / temperature, dim=-1)
            next_token = torch.multinomial(probs, num_samples=1)
        else:
            next_token = torch.argmax(next_token_logits, dim=-1, keepdim=True)

        current_len = 0
        code_buffer: list[list[torch.Tensor]] = [[] for _ in range(batch_size)]
        tokens_in_last_chunk = [0] * batch_size
        finished_mask = [False] * batch_size

        upsample_rate = self.model.speech_tokenizer.get_decode_upsample_rate()
        eos_token = c_conf.codec_eos_token_id
        try:
            codebook_limit = self.model.speech_tokenizer.model.config.decoder_config.codebook_size
        except Exception:
            codebook_limit = 4096

        def run_decode(stacked_codes, batch_indices, cut_indices):
            try:
                with torch.inference_mode():
                    if torch.cuda.is_available() and str(device).startswith('cuda'):
                        decode_stream = torch.cuda.Stream()
                        with torch.cuda.stream(decode_stream):
                            wavs_list, _ = self.model.speech_tokenizer.decode({'audio_codes': stacked_codes})
                        decode_stream.synchronize()
                    else:
                        wavs_list, _ = self.model.speech_tokenizer.decode({'audio_codes': stacked_codes})

                results = {}
                for local_i, global_b in enumerate(batch_indices):
                    wav = wavs_list[local_i]
                    cut = cut_indices[local_i]
                    if cut < len(wav):
                        chunk = wav[cut:]
                        if hasattr(chunk, 'detach'):
                            results[global_b] = chunk.detach().cpu().numpy()
                        else:
                            results[global_b] = np.asarray(chunk)
                    else:
                        results[global_b] = None
                return results
            except Exception as exc:
                return {'__error__': exc}

        try:
            while current_len < max_new_tokens:
                if all(finished_mask):
                    break

                if hasattr(torch, 'compiler') and hasattr(torch.compiler, 'cudagraph_mark_step_begin'):
                    torch.compiler.cudagraph_mark_step_begin()
                outputs = self.model.talker(
                    input_ids=next_token,
                    past_key_values=past_key_values,
                    past_hidden=past_hidden,
                    use_cache=True,
                    trailing_text_hidden=trailing_embeds,
                    tts_pad_embed=tts_pad_embed_batch,
                    generation_step=current_len,
                    subtalker_dosample=(temperature > 0),
                    subtalker_top_k=top_k,
                    subtalker_top_p=top_p,
                    subtalker_temperature=temperature,
                )

                past_key_values = outputs.past_key_values
                past_hidden = outputs.past_hidden
                full_codes = outputs.hidden_states[1]

                decode_candidates_codes = []
                decode_candidates_indices = []
                decode_candidates_cuts = []

                for batch_index in range(batch_size):
                    if finished_mask[batch_index]:
                        continue

                    token_id = full_codes[batch_index, 0].item()
                    if token_id == eos_token:
                        finished_mask[batch_index] = True
                        should_decode = bool(code_buffer[batch_index])
                    else:
                        code_buffer[batch_index].append(full_codes[batch_index : batch_index + 1].unsqueeze(1))
                        should_decode = len(code_buffer[batch_index]) >= (chunk_size + overlap)

                    if should_decode:
                        stacked = torch.cat(code_buffer[batch_index], dim=1)
                        stacked = torch.clamp(stacked, min=0, max=codebook_limit - 1)
                        decode_candidates_codes.append(stacked)
                        decode_candidates_indices.append(batch_index)

                        previous_kept = tokens_in_last_chunk[batch_index]
                        decode_candidates_cuts.append(previous_kept * upsample_rate)

                        if finished_mask[batch_index]:
                            code_buffer[batch_index] = []
                            tokens_in_last_chunk[batch_index] = 0
                        else:
                            code_buffer[batch_index] = code_buffer[batch_index][-overlap:]
                            tokens_in_last_chunk[batch_index] = overlap

                if decode_candidates_codes:
                    max_c_len = max(c.shape[1] for c in decode_candidates_codes)
                    padded_codes = []
                    for codes in decode_candidates_codes:
                        if codes.shape[1] < max_c_len:
                            pad = torch.zeros(
                                (1, max_c_len - codes.shape[1], codes.shape[2]),
                                device=device,
                                dtype=codes.dtype,
                            )
                            padded_codes.append(torch.cat([codes, pad], dim=1))
                        else:
                            padded_codes.append(codes)

                    batch_tensor = torch.cat(padded_codes, dim=0)
                    pending_decodes.append(
                        decode_executor.submit(
                            run_decode,
                            batch_tensor,
                            decode_candidates_indices,
                            decode_candidates_cuts,
                        )
                    )

                done_indices = []
                for index, future in enumerate(pending_decodes):
                    if not future.done():
                        continue
                    result = future.result()
                    if '__error__' in result:
                        raise result['__error__']
                    audio_yields = [None] * batch_size
                    has_yield = False
                    for batch_index, audio in result.items():
                        if audio is not None:
                            audio_yields[batch_index] = audio
                            has_yield = True
                    if has_yield:
                        yield audio_yields, list(finished_mask)
                    done_indices.append(index)

                for index in reversed(done_indices):
                    pending_decodes.pop(index)

                next_token_logits = outputs.logits[:, -1, :]
                if temperature > 0:
                    probs = torch.softmax(next_token_logits / temperature, dim=-1)
                    next_token = torch.multinomial(probs, num_samples=1)
                else:
                    next_token = torch.argmax(next_token_logits, dim=-1, keepdim=True)

                current_len += 1

            for future in concurrent.futures.as_completed(pending_decodes):
                result = future.result()
                if '__error__' in result:
                    raise result['__error__']
                audio_yields = [None] * batch_size
                has_yield = False
                for batch_index, audio in result.items():
                    if audio is not None:
                        audio_yields[batch_index] = audio
                        has_yield = True
                if has_yield:
                    yield audio_yields, list(finished_mask)
        finally:
            decode_executor.shutdown(wait=True)
