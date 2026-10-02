import { createElement, type SVGProps } from "react";
import { stickerArtwork, stickerPalette, type StickerIconName, type StickerShape } from "./stickerArtwork";

export type StickerIconProps = Omit<SVGProps<SVGSVGElement>, "name"> & {
  name: StickerIconName;
  size?: number;
  checkPathClassName?: string;
};

export type { StickerIconName } from "./stickerArtwork";

export function StickerIcon({ name, size = 24, className, checkPathClassName, ...props }: StickerIconProps) {
  const artwork = stickerArtwork[name];
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden={props["aria-label"] || props["aria-labelledby"] ? undefined : true}
      role={props["aria-label"] || props["aria-labelledby"] ? "img" : undefined}
      focusable="false"
      className={`sticker-icon${className ? ` ${className}` : ""}`}
      data-sticker-icon={name}
      data-sticker-tone={artwork.tone}
      {...props}
    >
      <g stroke={stickerPalette.ink} strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
        {(artwork.shapes as readonly StickerShape[]).map(({ tag, attributes }, index) => createElement(tag, {
          ...attributes,
          ...(checkPathClassName && attributes.className === "sticker-check-path"
            ? { className: `sticker-check-path ${checkPathClassName}` }
            : {}),
          key: index
        }))}
      </g>
    </svg>
  );
}
