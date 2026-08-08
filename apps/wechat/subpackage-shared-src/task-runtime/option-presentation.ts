import type { RichContentBlock } from "./content-renderer/model";

const OPTION_LABEL_PREFIX = /^([A-H])\s*[、.．:：]\s*/;

/** 仅剥离与当前选项 label 一致的开头前缀，不改题库原始数据。 */
export function stripOptionLabelPrefix(text: string, label: string): string {
  const match = OPTION_LABEL_PREFIX.exec(text);
  if (!match || match[1] !== label.trim().toUpperCase()) return text;
  return text.slice(match[0].length);
}

/** 只处理选项内容的第一个文本块，避免误删后续段落正文。 */
export function stripOptionContentPrefix(
  blocks: RichContentBlock[],
  label: string,
): RichContentBlock[] {
  let stripped = false;
  return blocks.map((block) => {
    if (stripped || block.type !== "paragraph" || !block.text) return block;
    stripped = true;
    const text = stripOptionLabelPrefix(block.text, label);
    return text === block.text ? block : { ...block, text };
  });
}
