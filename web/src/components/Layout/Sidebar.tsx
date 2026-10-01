// 侧边栏：模仿 codex1.html Sidebar 五段结构（品牌区 / New chat / 导航 / 知识库 / Recents / 底部状态）
// 功能区保留：新建会话、L2/L3 链路状态指示、检索调试与知识库管理占位菜单；
// Recents 区已接真实数据：展示后端 memory 持久化的历史会话（点击恢复、悬停删除）

import { useEffect, useState } from 'react';

import { fetchAgentHealth } from '../../api/agent';
import { ApiError } from '../../api/client';
import type { SessionItem } from '../../api/types';
import * as Icons from '../icons';

export type ServiceState = 'checking' | 'online' | 'degraded' | 'offline';

interface SidebarProps {
  /** 后端持久化的会话列表（按最近活跃降序） */
  sessions: SessionItem[];
  /** 当前会话 ID：列表高亮 */
  currentSessionId: string;
  /** 回答进行中禁用切换/删除，避免打断流式链路 */
  disabled?: boolean;
  onNewSession: () => void;
  onSelectSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string, title: string) => void;
}

/** 存档时间为后端本地时间 "YYYY-MM-DD HH:MM:SS"，转 T 分隔后按本地时区解析 */
function relativeTime(value: string | null): string {
  if (!value) return '';
  const d = new Date(value.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return '';
  const min = Math.floor((Date.now() - d.getTime()) / 60_000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day === 1) return '昨天';
  if (day < 7) return `${day} 天前`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
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

export default function Sidebar({
  sessions,
  currentSessionId,
  disabled = false,
  onNewSession,
  onSelectSession,
  onDeleteSession,
}: SidebarProps) {
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

      {/* 历史会话（原 Recents 区）：数据源升级为后端 memory 持久化会话，点击恢复、悬停删除 */}
      <div className="sidebar-scroll flex-1 overflow-y-auto px-3 py-2">
        <div className="mb-2 text-xs font-medium text-faint">历史会话</div>
        {sessions.length === 0 ? (
          <div className="px-2 py-1.5 text-sm text-faint">
            {disabled ? '回答进行中，稍后展示…' : '暂无已存档的会话'}
          </div>
        ) : (
          <div className="space-y-1">
            {sessions.map((s) => {
              const active = s.session_id === currentSessionId;
              return (
                <div
                  key={s.session_id}
                  title={s.title || s.session_id}
                  onClick={() => !disabled && !active && onSelectSession(s.session_id)}
                  className={`group flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm ${
                    active ? 'bg-raised text-ink' : 'text-muted hover:bg-raised'
                  } ${disabled && !active ? 'pointer-events-none opacity-60' : ''}`}
                >
                  <span className="min-w-0 flex-1 truncate">{s.title || '（未命名会话）'}</span>
                  <span className="shrink-0 font-mono text-xs text-faint group-hover:hidden">
                    {relativeTime(s.updated_at)}
                  </span>
                  <button
                    className="hidden shrink-0 text-faint hover:text-err group-hover:block"
                    title="删除会话及其历史存档"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!disabled) onDeleteSession(s.session_id, s.title);
                    }}
                  >
                    <Icons.Trash />
                  </button>
                </div>
              );
            })}
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
