import {
  Pill,
  TextbookPreview
} from "../../components/ui";

export function SourceSheetContent({ title, page, image }: { title: string; page: string; image: string }) {
  return (
    <div className="sheet-body">
      <Pill tone="purple">{page}</Pill>
      <h3>{title}</h3>
      <TextbookPreview src={image} title={`${title} ${page}`} />
      <p className="helper-text">此处展示教材页截图，仅用于来源引用和章节保真，不导出整本原书内容。</p>
    </div>
  );
}
