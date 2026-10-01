// 问答页：模仿 codex1.html App 布局——Header 顶栏 + (Sidebar | Main 引导/消息 + 输入区)

import { useState } from 'react';

import InputBox from '../components/Chat/InputBox';
import MessageList from '../components/Chat/MessageList';
import Header from '../components/Layout/Header';
import Sidebar from '../components/Layout/Sidebar';
import { useChat } from '../hooks/useChat';

export default function ChatPage() {
  const { messages, status, recents, send, newSession } = useChat();
  // 「自动溯源」开关：开启后新回答返回即展开作答依据面板
  const [autoCite, setAutoCite] = useState(false);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-white">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar recents={recents} onNewSession={newSession} />
        <main className="flex min-w-0 flex-1 flex-col bg-white">
          <div className="flex-1 overflow-y-auto px-6 py-6">
            <div className="mx-auto h-full max-w-3xl">
              <MessageList messages={messages} autoCite={autoCite} />
            </div>
          </div>
          <InputBox
            disabled={status === 'sending'}
            autoCite={autoCite}
            onToggleAutoCite={() => setAutoCite((v) => !v)}
            onSend={send}
          />
        </main>
      </div>
    </div>
  );
}
