export interface MaterialTextParagraph {
  paragraphId: string;
  text: string;
  paragraphIndex: number;
}

export function materialTextParagraphs(
  text: string | null | undefined,
  sourceId: string,
): MaterialTextParagraph[] {
  const normalizedText = text ?? "";
  const paragraphs = normalizedText
    .split(/\r?\n+/u)
    .map((item) => item.trim())
    .filter(Boolean);
  const source = paragraphs.length > 0 ? paragraphs : [normalizedText.trim()].filter(Boolean);
  return source.map((paragraph, index) => ({
    paragraphId: `${sourceId}-paragraph-${String(index + 1)}`,
    text: paragraph,
    paragraphIndex: index + 1,
  }));
}
