/* console/MsgView.tsx — 指挥部消息通道（I-158）：留痕传话与群聊视图。
 * 每条消息五要素：发送方、接收方、时刻、关联事项号、正文；
 * 发送走 /api/console/message（消息表加事件台账双写）。 */
import { useCallback, useEffect, useState } from "react";

interface Msg {
  seq: number;
  ts: string;
  sender: string;
  recipient: string;
  item_id: string | null;
  body: string;
}

const LANES = ["HQ", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10"];

export default function MsgView({ viewer }: { viewer: string }) {
  const [msgs, setMsgs] = useState<Msg[] | null>(null);
  const [recipient, setRecipient] = useState("HQ");
  const [itemId, setItemId] = useState("");
  const [body, setBody] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/console/messages");
      if (r.status === 401) { location.href = "/login"; return; }
      const d = await r.json();
      if (d.ok) { setMsgs(d.result.messages); setFailed(false); }
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  const send = async () => {
    if (!body.trim()) return;
    try {
      const r = await fetch("/api/console/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recipient, item_id: itemId.trim(), body: body.trim() }),
      });
      const d = await r.json();
      if (d.ok) {
        setNote("已发送（消息 #" + d.result.seq + "，台账已记）");
        setBody("");
        refresh();
      } else {
        setNote("失败：" + (d.error ?? r.status));
      }
    } catch (e) {
      setNote("失败：" + String(e));
    }
  };

  return (
    <div className="msg">
      <div className="msg-stream" aria-label="消息流">
        {failed ? (
          <p className="ws-none">消息通道不可达，已停用旧消息显示。</p>
        ) : msgs == null ? (
          <p className="ws-none">消息加载中…</p>
        ) : msgs.length === 0 ? (
          <p className="ws-none">还没有消息，下面发出第一条。</p>
        ) : (
          msgs.map((m) => (
            <div key={m.seq} className="msg-row">
              <span className="msg-sender">{m.sender}</span>
              <span className="msg-arrow">→</span>
              <span className="msg-recipient">{m.recipient}</span>
              {m.item_id && <span className="msg-item">{m.item_id}</span>}
              <span className="msg-ts">{m.ts.replace("T", " ")}</span>
              <p className="msg-body">{m.body}</p>
            </div>
          ))
        )}
      </div>
      <div className="msg-compose">
        <div className="msg-compose-row">
          <span className="msg-sender-chip">{viewer}</span>
          <span className="msg-arrow">→</span>
          <select className="msg-select" value={recipient}
                  onChange={(e) => setRecipient(e.target.value)}
                  aria-label="接收方路线">
            {LANES.map((l) => <option key={l}>{l}</option>)}
          </select>
          <input className="msg-item-input" value={itemId}
                 onChange={(e) => setItemId(e.target.value)}
                 placeholder="关联事项号（可空，如 I-131）" aria-label="关联事项号" />
        </div>
        <textarea className="msg-input" value={body} rows={3}
                  onChange={(e) => setBody(e.target.value)}
                  placeholder="消息正文（入台账留痕）" aria-label="消息正文" />
        <div className="msg-compose-row">
          <button className="lab-btn" disabled={!body.trim()} onClick={send}>发送</button>
          {note && <span className="msg-note">{note}</span>}
        </div>
      </div>
    </div>
  );
}
