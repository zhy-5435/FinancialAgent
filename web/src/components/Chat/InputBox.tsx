// 底部输入交互区：模仿 codex1.html MainContent 下半部
// project chip + rounded-2xl 阴影输入条（Plus / Ask-for-approval 位 → 自动溯源开关 / 深灰发送按钮）

import { useCallback, useState } from 'react';

import * as Icons from '../icons';

export const MAX_QUESTION_LEN = 500;

interface InputBoxProps {
  disabled: boolean;
  autoCite: boolean;
  onToggleAutoCite: () => void;
  onSend: (question: string) => void;
}

export default function InputBox({ disabled, autoCite, onToggleAutoCite, onSend }: InputBoxProps) {
  const [value, setValue] = useState('');
  const tooLong = value.length > MAX_QUESTION_LEN;
  const canSend = Boolean(value.trim()) && !tooLong && !disabled;

  const submit = useCallback(() => {
    if (!canSend) return;
    onSend(value.trim());
    setValue('');
  }, [canSend, value, onSend]);

  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-3xl space-y-2">
        {/* 示例同款 Choose project chip：这里固定为全库检索范围说明 */}
        <button className="flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 text-sm text-muted hover:bg-raised">
          <Icons.Folder />
          知识库：监管规则 / 内部制度 / 产品说明书
        </button>

        {/* 示例同款圆角输入条 */}
        <div className="flex items-end gap-2 rounded-2xl border border-line bg-white p-3 shadow-sm transition-shadow hover:shadow-md focus-within:shadow-md">
          <div className="flex items-center gap-3 pb-1">
            <button
              className="cursor-not-allowed rounded-lg p-1.5 text-faint"
              title="附件上传（规划中）"
            >
              <Icons.Plus />
            </button>
            {/* 示例「Ask for approval」位：落地为自动展开溯源面板开关 */}
            <button
              onClick={onToggleAutoCite}
              className={`flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm transition-colors ${
                autoCite ? 'bg-accent/10 text-accent' : 'bg-raised text-muted hover:bg-raised/70'
              }`}
              title="回答后自动展开作答依据"
            >
              <Icons.Hand />
              自动溯源
            </button>
          </div>

          <input
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit()}
            placeholder="随便问：条款、费率、风险等级、制度流程…"
            className="flex-1 bg-transparent px-2 py-1 text-base text-ink outline-none placeholder:text-faint"
          />

          <div className="flex items-center gap-3 pb-1">
            <span className={`text-xs ${tooLong ? 'text-err' : 'text-faint'}`}>
              {value.length} / {MAX_QUESTION_LEN}
            </span>
            <button
              onClick={submit}
              disabled={!canSend}
              className="rounded-lg bg-ink p-2 text-white transition-colors hover:bg-shade disabled:cursor-not-allowed disabled:opacity-40"
              title="发送"
            >
              <Icons.Send />
            </button>
          </div>
        </div>

        <p className="text-center text-xs text-faint">
          回答仅基于 L1 知识库检索结果生成并强制溯源，不构成投资建议
        </p>
      </div>
    </div>
  );
}
