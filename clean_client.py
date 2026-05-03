import re

with open("x:/dev/G3_QWEN_TTS/frontend/src/api/client.ts", "r", encoding="utf8") as f:
    text = f.read()

# 1. Remove from imports
text = re.sub(r'^\s*BenchmarkRun,?\n?', '', text, flags=re.MULTILINE)
text = re.sub(r'^\s*BenchmarkRunDraft,?\n?', '', text, flags=re.MULTILINE)

# 2. Remove from defaultSnapshot
text = re.sub(r'^\s*benchmarkRuns:\s*\[\],\n?', '', text, flags=re.MULTILINE)

# 3. Remove backend benchmark interfaces
text = re.sub(r'interface BackendBenchmarkIteration\s*\{.*?\n\}\n', '', text, flags=re.DOTALL)
text = re.sub(r'interface BackendBenchmarkCase\s*\{.*?\n\}\n', '', text, flags=re.DOTALL)
text = re.sub(r'interface BackendBenchmarkRunResponse\s*\{.*?\n\}\n', '', text, flags=re.DOTALL)

# 4. Remove mapBenchmarkRun
text = re.sub(r'function mapBenchmarkRun\(.*?(?=function mapOverview)', '', text, flags=re.DOTALL)

# 5. Remove from safeFetch calls
text = re.sub(r'^\s*safeFetch<BackendBenchmarkRunResponse\[\]>\(.*?\),\n?', '', text, flags=re.MULTILINE)
text = re.sub(r'benchmarkRunsResult,\s*', '', text, flags=re.MULTILINE)
text = re.sub(r',\s*benchmarkRunsResult', '', text, flags=re.MULTILINE)

# 6. Remove benchmarkRuns from snapshot return
text = re.sub(r'^\s*benchmarkRuns:\s*benchmarkRunsResult\.data\.map.*?,\n?', '', text, flags=re.MULTILINE)

# Write it back
with open("x:/dev/G3_QWEN_TTS/frontend/src/api/client.ts", "w", encoding="utf8") as f:
    f.write(text)
