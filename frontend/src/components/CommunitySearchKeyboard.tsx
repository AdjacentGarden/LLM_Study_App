import { ArrowBigUp, CornerDownLeft, Delete, Mic, Smile } from "lucide-react";
import { useState } from "react";

const keyboardRows = [
  ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
  ["z", "x", "c", "v", "b", "n", "m"]
] as const;

const searchSuggestions = ["生物", "数学", "物理"] as const;

type CommunitySearchKeyboardProps = {
  onBackspace: () => void;
  onClose: () => void;
  onInsert: (value: string) => void;
  onSuggestion: (value: string) => void;
};

export function CommunitySearchKeyboard({
  onBackspace,
  onClose,
  onInsert,
  onSuggestion
}: CommunitySearchKeyboardProps) {
  const [shifted, setShifted] = useState(false);

  function insertLetter(letter: string) {
    onInsert(shifted ? letter.toUpperCase() : letter);
    if (shifted) setShifted(false);
  }

  return (
    <section
      className="community-search-keyboard"
      id="community-search-keyboard"
      aria-label="课程搜索键盘"
      data-open="true"
    >
      <div className="community-keyboard-suggestions" aria-label="搜索建议">
        {searchSuggestions.map((suggestion) => (
          <button
            type="button"
            key={suggestion}
            aria-label={`建议：${suggestion}`}
            tabIndex={-1}
            onClick={() => onSuggestion(suggestion)}
          >
            {suggestion}
          </button>
        ))}
      </div>

      <div className="community-keyboard-keys" role="group" aria-label="字母键">
        <div className="community-keyboard-row community-keyboard-row-top">
          {keyboardRows[0].map((letter) => (
            <button type="button" key={letter} aria-label={`字母 ${letter}`} tabIndex={-1} onClick={() => insertLetter(letter)}>
              {shifted ? letter.toUpperCase() : letter}
            </button>
          ))}
        </div>

        <div className="community-keyboard-row community-keyboard-row-middle">
          {keyboardRows[1].map((letter) => (
            <button type="button" key={letter} aria-label={`字母 ${letter}`} tabIndex={-1} onClick={() => insertLetter(letter)}>
              {shifted ? letter.toUpperCase() : letter}
            </button>
          ))}
        </div>

        <div className="community-keyboard-row community-keyboard-row-lower">
          <button
            className="community-keyboard-function-key"
            type="button"
            aria-label={shifted ? "关闭大写" : "开启大写"}
            aria-pressed={shifted}
            tabIndex={-1}
            onClick={() => setShifted((active) => !active)}
          >
            <ArrowBigUp size={21} strokeWidth={1.9} aria-hidden="true" />
          </button>
          {keyboardRows[2].map((letter) => (
            <button type="button" key={letter} aria-label={`字母 ${letter}`} tabIndex={-1} onClick={() => insertLetter(letter)}>
              {shifted ? letter.toUpperCase() : letter}
            </button>
          ))}
          <button
            className="community-keyboard-function-key"
            type="button"
            aria-label="退格"
            tabIndex={-1}
            onClick={onBackspace}
          >
            <Delete size={21} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </div>

        <div className="community-keyboard-row community-keyboard-row-bottom">
          <button
            className="community-keyboard-function-key community-keyboard-abc-key"
            type="button"
            aria-label="字母键盘"
            tabIndex={-1}
            onClick={() => setShifted(false)}
          >
            ABC
          </button>
          <button
            className="community-keyboard-space-key"
            type="button"
            aria-label="空格"
            tabIndex={-1}
            onClick={() => onInsert(" ")}
          />
          <button
            className="community-keyboard-submit-key"
            type="button"
            aria-label="完成搜索"
            tabIndex={-1}
            onClick={onClose}
          >
            <CornerDownLeft size={22} strokeWidth={1.9} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="community-keyboard-utility" aria-label="键盘辅助功能">
        <button type="button" aria-label="插入表情" tabIndex={-1} onClick={() => onInsert("😊")}>
          <Smile size={23} strokeWidth={1.8} aria-hidden="true" />
        </button>
        <button type="button" aria-label="语音输入暂不可用" tabIndex={-1} disabled>
          <Mic size={23} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
    </section>
  );
}
