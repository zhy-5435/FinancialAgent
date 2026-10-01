// 会话历史管理：对接 L3 memory 持久化接口（GET /sessions、/sessions/{id}/messages、DELETE）
// 与 useChat 分工：本 hook 只管会话档案（列表/恢复/删除），消息流与当前会话 ID 由 useChat 持有

import { useCallback, useEffect, useState } from 'react';

import { deleteSession, fetchSessionMessages, fetchSessions } from '../api/agent';
import type { SessionItem } from '../api/types';
import { toRestoredChatMessages, type ChatMessage } from './useChat';

export function useSessions() {
  const [sessions, setSessions] = useState<SessionItem[]>([]);

  /** 拉取会话列表；后端不可达时静默保留旧数据（侧栏状态灯另有探活提示） */
  const refresh = useCallback(async () => {
    try {
      setSessions(await fetchSessions());
    } catch (e) {
      console.warn('会话列表刷新失败', e);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  /** 恢复指定会话的可见历史消息（仅 user/assistant，按时间升序） */
  const loadMessages = useCallback(async (sessionId: string): Promise<ChatMessage[]> => {
    const resp = await fetchSessionMessages(sessionId);
    return toRestoredChatMessages(resp.messages);
  }, []);

  /** 删除会话存档并刷新列表；若删的是当前会话，由页面层负责重置新会话 */
  const remove = useCallback(async (sessionId: string) => {
    await deleteSession(sessionId);
    await refresh();
  }, [refresh]);

  return { sessions, refresh, loadMessages, remove };
}
