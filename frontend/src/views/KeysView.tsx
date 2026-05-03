import { useState } from 'react';
import { Panel } from '../components/Panel';
import { SectionHeader } from '../components/SectionHeader';
import type { ApiKeyRecord } from '../types';
import { formatDate } from '../lib/format';

interface KeysViewProps {
  adminRecord: ApiKeyRecord | null;
  currentAdminKey: string;
  onRotateAdminKey: () => Promise<ApiKeyRecord | void>;
  onLogout: () => void;
}

async function copyToClipboard(value: string) {
  if (!value) {
    return;
  }
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }

  const textArea = document.createElement('textarea');
  textArea.value = value;
  textArea.setAttribute('readonly', 'readonly');
  textArea.style.position = 'fixed';
  textArea.style.opacity = '0';
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand('copy');
  document.body.removeChild(textArea);
}

export function KeysView({ adminRecord, currentAdminKey, onRotateAdminKey, onLogout }: KeysViewProps) {
  const [newKey, setNewKey] = useState<ApiKeyRecord | null>(null);

  async function handleRotate() {
    const result = await onRotateAdminKey();
    if (result?.rawKey) {
      setNewKey(result);
    }
  }

  return (
    <div className="view-grid">
      <SectionHeader
        title="Admin Key"
        subtitle="Keep dashboard access aligned with the TADA operator pattern: visible metadata, copy actions, and safe key rotation."
      />
      {newKey && (
        <Panel title="New Admin Key" subtitle="The backend returns the rotated key only once. Copy it before leaving this page.">
          <div className="access-key-banner">
            <code className="access-key-value">{newKey.rawKey}</code>
            <button className="secondary-button" type="button" onClick={() => void copyToClipboard(newKey.rawKey || '')}>
              Copy
            </button>
          </div>
        </Panel>
      )}
      <div className="two-column keys-layout">
        <Panel title="Dashboard Access" subtitle="The browser keeps the current operator token locally until you rotate it or clear this session.">
          <div className="metric-grid compact">
            <div className="metric-card">
              <span>Name</span>
              <strong>{adminRecord?.name || 'Master Admin Key'}</strong>
            </div>
            <div className="metric-card">
              <span>Created</span>
              <strong>{adminRecord ? formatDate(adminRecord.createdAt) : 'Unknown'}</strong>
            </div>
            <div className="metric-card">
              <span>Last used</span>
              <strong>{adminRecord ? formatDate(adminRecord.lastUsedAt) : 'Unknown'}</strong>
            </div>
            <div className="metric-card">
              <span>Browser token</span>
              <strong>{currentAdminKey ? 'Stored' : 'Missing'}</strong>
            </div>
          </div>
          <div className="access-key-banner admin-key-banner">
            <div className="stack compact">
              <strong>Current browser key</strong>
              <code className="access-key-value">{currentAdminKey || 'No key stored in this browser session.'}</code>
            </div>
            <button className="secondary-button" type="button" onClick={() => void copyToClipboard(currentAdminKey)}>
              Copy Current Key
            </button>
          </div>
        </Panel>

        <Panel title="Operator Actions" subtitle="Rotate the admin key when ownership changes, then clear the browser token on shared machines.">
          <div className="form-grid operator-actions">
            <button className="primary-button" type="button" onClick={() => void handleRotate()}>
              Rotate Admin Key
            </button>
            <button className="secondary-button" type="button" onClick={() => void copyToClipboard(currentAdminKey)}>
              Copy Current Key
            </button>
            <button className="ghost-button" type="button" onClick={onLogout}>
              Clear Browser Key
            </button>
            <div className="callout subtle-callout">
              <strong>Handover rule</strong>
              <p>Rotate the key first when another operator takes over. The browser automatically switches to the new token after rotation.</p>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
