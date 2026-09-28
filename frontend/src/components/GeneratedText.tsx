import { structureGeneratedText } from "./textStructure";

export function GeneratedText({
  value,
  className = "",
}: {
  value: string;
  className?: string;
}) {
  const blocks = structureGeneratedText(value);
  return (
    <div className={`generated-text ${className}`.trim()}>
      {blocks.map((block, index) => {
        if (block.kind === "paragraph") {
          return <p key={`${index}-${block.text.slice(0, 24)}`}>{block.text}</p>;
        }
        const List = block.kind === "ordered-list" ? "ol" : "ul";
        return (
          <List key={`${index}-${block.items[0]?.slice(0, 24) ?? "list"}`}>
            {block.items.map((item, itemIndex) => (
              <li key={`${itemIndex}-${item.slice(0, 24)}`}>{item}</li>
            ))}
          </List>
        );
      })}
    </div>
  );
}
