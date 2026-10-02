// 问答页：模仿 codex1.html App 布局——(Sidebar | Main 引导/消息 + 输入区)
// 会话记忆接线：侧栏历史会话 = 后端 memory 存档；回合结束刷新列表，选旧会话即恢复历史

import { useCallback, useEffect, useState } from 'react';

import { ApiError } from '../api/client';
import InputBox from '../components/Chat/InputBox';
import MessageList from '../components/Chat/MessageList';
import Sidebar from '../components/Layout/Sidebar';
import { useChat } from '../hooks/useChat';
import { useSessions } from '../hooks/useSessions';

export default function ChatPage() {
  // 「自动溯源」开关：开启后新回答返回即展开作答依据面板
  const [autoCite, setAutoCite] = useState(false);
  // 会话恢复/删除失败提示：轻量横幅，不污染消息流
  const [sessionNotice, setSessionNotice] = useState<string | null>(null);

  const { sessions, refresh, loadMessages, remove } = useSessions();
  const { messages, status, sessionId, restoredId, send, newSession, switchTo } = useChat({
    onTurnEnd: refresh,
  });
  const sending = status === 'sending';

  // 整页重载（切到其他 App 后再进入致页面重新挂载）后，据持久化的活跃会话恢复原对话：
  // 仅首挂载执行一次——拉取存档历史回填消息流；会话已删或为空则维持当前会话，不打断用户
  useEffect(() => {
    if (!restoredId) return;
    let cancelled = false;
    loadMessages(restoredId)
      .then((restored) => {
        if (!cancelled && restored.length > 0) switchTo(restoredId, restored);
      })
      .catch(() => {
        /* 后端存档不可达/已删除：保持当前会话，不弹错误打断首屏 */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const describeError = (e: unknown, fallback: string) =>
    e instanceof ApiError ? `${fallback}：${e.detail}` : fallback;

  /** 侧栏点击历史会话：拉取存档消息并切换当前会话（回答进行中禁点） */
  const handleSelectSession = useCallback(
    async (id: string) => {
      if (sending) return;
      try {
        const restored = await loadMessages(id);
        switchTo(id, restored);
        setSessionNotice(null);
      } catch (e) {
        setSessionNotice(describeError(e, '恢复会话失败'));
      }
    },
    [sending, loadMessages, switchTo],
  );

  /** 侧栏删除会话：二次确认；删的是当前会话则顺便开新会话 */
  const handleDeleteSession = useCallback(
    async (id: string, title: string) => {
      if (sending) return;
      if (!window.confirm(`删除会话「${title || '未命名'}」及其历史存档？该操作不可恢复`)) return;
      try {
        await remove(id);
        if (id === sessionId) newSession();
        setSessionNotice(null);
      } catch (e) {
        setSessionNotice(describeError(e, '删除会话失败'));
      }
    },
    [sending, remove, sessionId, newSession],
  );

  /** 新建会话：重置消息流与会话 ID，并清除上一次遗留的失败提示 */
  const handleNewSession = useCallback(() => {
    newSession();
    setSessionNotice(null);
  }, [newSession]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-white">
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          sessions={sessions}
          currentSessionId={sessionId}
          disabled={sending}
          onNewSession={handleNewSession}
          onSelectSession={handleSelectSession}
          onDeleteSession={handleDeleteSession}
        />
        <main className="flex min-w-0 flex-1 flex-col bg-white">
          {sessionNotice && (
            <div className="mx-6 mt-3 flex items-center justify-between rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-xs text-warn">
              <span>{sessionNotice}</span>
              <button className="text-faint hover:text-warn" onClick={() => setSessionNotice(null)}>
                关闭
              </button>
            </div>
          )}
          <div className="flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto h-full max-w-3xl">
              <MessageList messages={messages} autoCite={autoCite} />
            </div>
          </div>
          <InputBox
            disabled={sending}
            autoCite={autoCite}
            onToggleAutoCite={() => setAutoCite((v) => !v)}
            onSend={send}
          />
        </main>
      </div>
    </div>
  );
}
