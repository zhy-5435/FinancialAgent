// 侧边栏：模仿 codex1.html Sidebar 五段结构（品牌区 / New chat / 导航 / 知识库 / Recents / 底部状态）
// 功能区保留：新建会话、L2/L3 链路状态指示、检索调试与知识库管理占位菜单

import { useEffect, useState } from 'react';

import { fetchAgentHealth } from '../../api/agent';
import { ApiError } from '../../api/client';
import * as Icons from '../icons';

export type ServiceState = 'checking' | 'online' | 'degraded' | 'offline';

interface SidebarProps {
  recents: string[];
  onNewSession: () => void;
}

/** 底部状态徽章：示例同款 rounded-full 蓝底白字，异常时切换语义色 */
function badge(state: ServiceState): { text: string; cls: string } {
  switch (state) {
    case 'online':
      return { text: '服务正常', cls: 'bg-accent text-white' };
    case 'degraded':
      return { text: 'L2 不可达', cls: 'bg-warn text-white' };
    case 'offline':
      return { text: 'L3 不可达', cls: 'bg-err text-white' };
    default:
      return { text: '检测中', cls: 'bg-raised text-muted' };
  }
}

export default function Sidebar({ recents, onNewSession }: SidebarProps) {
  const [state, setState] = useState<ServiceState>('checking');

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const h = await fetchAgentHealth();
        if (alive) setState(h.search_api_reachable ? 'online' : 'degraded');
      } catch (e) {
        // 502/网络失败均视为链路问题
        if (alive) setState(e instanceof ApiError && e.status === 502 ? 'degraded' : 'offline');
      }
    };
    check();
    const timer = setInterval(check, 30_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const dot = badge(state);

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-surface">
      {/* 顶部品牌区：示例同款「名称 + ChevronDown | Search」 */}
      <div className="border-b border-line p-3">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-1 text-sm font-medium text-ink">
            Financial Agent
            <Icons.ChevronDown />
          </div>
          <button className="text-faint hover:text-muted" title="搜索会话（规划中）">
            <Icons.Search />
          </button>
        </div>
        <button
          onClick={onNewSession}
          className="flex w-full items-center gap-2 rounded-md border border-line bg-white px-3 py-2 text-sm text-muted hover:bg-surface"
        >
          <Icons.NewChat />
          新建会话
        </button>
      </div>

      {/* 导航菜单：功能区占位菜单平移进示例样式 */}
      <div className="py-2">
        <div className="flex cursor-pointer items-center gap-2 bg-raised px-3 py-2 text-sm text-ink">
          <Icons.Book />
          知识问答
        </div>
        <div className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm text-muted hover:bg-raised">
          <Icons.At />
          检索调试
          <span className="text-xs text-faint">（规划中）</span>
        </div>
        <div className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm text-muted hover:bg-raised">
          <Icons.More />
          知识库管理
          <span className="text-xs text-faint">（规划中）</span>
        </div>
      </div>

      {/* 知识库范围：对应示例 Projects 区 */}
      <div className="px-3 py-2">
        <div className="mb-2 flex items-center gap-1 text-xs font-medium text-faint">
          知识库
          <Icons.ChevronDown />
        </div>
        <div className="space-y-1">
          {['监管规则', '内部制度', '产品说明书'].map((name, i) => (
            <div
              key={name}
              className={`cursor-pointer rounded px-2 py-1.5 ${
                i === 0 ? 'bg-raised' : 'hover:bg-raised'
              }`}
            >
              <div className="flex items-center justify-between text-sm text-muted">
                <div className="flex items-center gap-2">
                  <Icons.Folder />
                  {name}
                </div>
                {i === 0 && (
                  <span className="flex items-center gap-1 text-faint">
                    <Icons.More />
                    <Icons.Edit />
                  </span>
                )}
              </div>
              <div className="ml-6 text-xs text-faint">已启用检索</div>
            </div>
          ))}
        </div>
      </div>

      {/* 最近提问：对应示例 Recents */}
      <div className="sidebar-scroll flex-1 overflow-y-auto px-3 py-2">
        <div className="mb-2 text-xs font-medium text-faint">最近提问</div>
        {recents.length === 0 ? (
          <div className="px-2 py-1.5 text-sm text-faint">暂无记录</div>
        ) : (
          <div className="space-y-1">
            {recents.map((item, idx) => (
              <div
                key={`${item}-${idx}`}
                title={item}
                className="cursor-pointer truncate rounded px-2 py-1.5 text-sm text-muted hover:bg-raised"
              >
                {item}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 底部状态栏：示例同款 Settings + 右侧圆角徽章 */}
      <div className="flex items-center justify-between border-t border-line p-3">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Icons.Settings />
          问答引擎
        </div>
        <span className={`rounded-full px-2.5 py-0.5 text-xs ${dot.cls}`}>{dot.text}</span>
      </div>
    </aside>
  );
}
