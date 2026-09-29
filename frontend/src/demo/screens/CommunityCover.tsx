import { FileText } from "lucide-react";
import { useImageMotion } from "../motion";

export function CommunityCover({
  source,
  title,
  variant
}: {
  source: string | null | undefined;
  title: string;
  variant: "tile" | "detail";
}) {
  const imageMotion = useImageMotion(source);
  const className = variant === "tile" ? "community-book-cover" : "community-detail-cover";

  if (!source || imageMotion.state === "failed") {
    return (
      <span
        className={`community-cover-fallback ${className}-fallback`}
        data-motion-image-source={source ?? undefined}
        data-motion-image-state="failed"
        role="img"
        aria-label={`${title} 封面不可用`}
      >
        <FileText size={variant === "tile" ? 22 : 30} aria-hidden="true" />
      </span>
    );
  }

  return (
    <img
      className={`community-cover-image ${className}`}
      src={source}
      alt={`${title} 封面`}
      ref={imageMotion.imageRef}
      data-motion-image-source={source}
      data-motion-image-state={imageMotion.state}
      onLoad={imageMotion.onLoad}
      onError={imageMotion.onError}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && event.animationName === "motion-stage3-image-in") imageMotion.settleAnimation();
      }}
    />
  );
}
