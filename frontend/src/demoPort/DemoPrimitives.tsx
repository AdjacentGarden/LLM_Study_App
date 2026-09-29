import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ComponentPropsWithoutRef, ReactNode } from "react";
import { StateSwapText } from "../demo/motion";

type DemoButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "text" | "danger";
  icon?: ReactNode;
  loading?: boolean;
};

export function Button({ variant = "primary", icon, loading, className = "", children, ...props }: DemoButtonProps) {
  return <button className={`button button-${variant} ${className}`} type={props.type ?? "button"} {...props}>
    {loading ? <Loader2 className="spin" size={18} aria-hidden="true" /> : icon}
    <span>{typeof children === "string" && loading ? <StateSwapText value={children} reserveValues={[children]} /> : children}</span>
  </button>;
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const progress = Math.max(0, Math.min(100, value));
  return <div className="progress-wrap" role="progressbar" aria-label={label ?? `进度 ${progress}%`}
    aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-valuetext={label}>
    <div className="progress-track" aria-hidden="true"><div className="progress-fill" style={{ transform: `scaleX(${progress / 100})` }} /></div>
    {label ? <span>{label}</span> : null}
  </div>;
}

export function Card({ children, className = "", surface = "default", ...props }: ComponentPropsWithoutRef<"section"> & { children: ReactNode; surface?: "default" | "elevated" | "celebration" }) {
  return <section className={`card card-surface-${surface} ${className}`} data-surface={surface} {...props}>{children}</section>;
}

export function Pill({ children, tone = "purple" }: { children: ReactNode; tone?: string }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}
