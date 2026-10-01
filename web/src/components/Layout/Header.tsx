// 顶部导航栏：模仿 codex1.html Header（h-10 白底细边框，左品牌 + 菜单，右功能区）

import * as Icons from '../icons';

export default function Header() {
  return (
    <header className="flex h-10 shrink-0 items-center justify-between border-b border-line bg-white px-3">
      <div className="flex items-center gap-4 text-sm text-muted">
        <nav className="flex gap-4">
          {/* 功能区菜单：当前以问答页为主入口，其余为后续页面占位 */}
          <span className="cursor-pointer text-ink">Chat</span>
          <span className="cursor-pointer opacity-60 hover:text-ink">Knowledge</span>
          <span className="cursor-pointer hover:text-ink">History</span>
          <span className="cursor-pointer hover:text-ink">Help</span>
        </nav>
      </div>
      <div className="flex items-center gap-2 text-faint">
        <button className="flex h-8 w-8 items-center justify-center rounded hover:bg-raised">
          <Icons.Settings />
        </button>
      </div>
    </header>
  );
}
