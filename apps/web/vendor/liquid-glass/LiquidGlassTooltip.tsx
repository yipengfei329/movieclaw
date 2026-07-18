import { useEffect, useId, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const TOOLTIP_PAD_X = 16;
const TOOLTIP_PAD_Y = 10;

export interface LiquidGlassTooltipProps {
  backgroundImage: string;
  content: React.ReactNode;
  children: React.ReactNode;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  placement?: "top" | "right" | "bottom" | "left";
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  className?: string;
}

export function LiquidGlassTooltip({
  backgroundImage,
  content,
  children,
  open,
  defaultOpen = false,
  onOpenChange,
  placement = "top",
  variant = "dark",
  settings,
  className = ""
}: LiquidGlassTooltipProps) {
  const id = useId();
  const tooltipRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const closeTimerRef = useRef<number | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const motionRef = useRef({ stretch: 0, target: 0, velocity: 0 });
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const [mounted, setMounted] = useState(open ?? defaultOpen);
  const currentOpen = open ?? internalOpen;
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, {
    blur: .26,
    refraction: .34,
    chromaticAberration: .028,
    distortion: .012,
    edgeHighlight: .12,
    specular: .16,
    fresnel: 1,
    depth: 30,
    brightness: -.08,
    saturation: -.08,
    darkTint: .38,
    tintStrength: .12,
    opacity: 1,
    shadow: 0,
    bevel: 0,
    ...settings
  }), [settings, variant]);

  const commitOpen = (next: boolean) => {
    if (open === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };

  const syncMotion = (stretch: number, pressed: boolean) => {
    const { width, height } = sizeRef.current;
    if (!width || !height) return;
    rendererRef.current?.setGeometry(
      width / 2 + TOOLTIP_PAD_X,
      height / 2 + TOOLTIP_PAD_Y,
      stretch,
      pressed,
      1,
      1,
      0
    );
  };

  const animateMotion = () => {
    const motion = motionRef.current;
    motion.velocity += (motion.target - motion.stretch) * mergedSettings.liquidSpring;
    motion.velocity *= mergedSettings.liquidDamping;
    motion.stretch = Math.max(-.025, Math.min(.08, motion.stretch + motion.velocity));
    motion.target *= .68;
    syncMotion(motion.stretch, currentOpen);
    if (Math.abs(motion.target) + Math.abs(motion.stretch) + Math.abs(motion.velocity) > .001) {
      frameRef.current = requestAnimationFrame(animateMotion);
    } else {
      motion.target = 0;
      motion.stretch = 0;
      motion.velocity = 0;
      syncMotion(0, false);
    }
  };

  useEffect(() => {
    if (currentOpen) {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
      setMounted(true);
      motionRef.current.target = .035;
      cancelAnimationFrame(frameRef.current);
      frameRef.current = requestAnimationFrame(animateMotion);
      return;
    }
    closeTimerRef.current = window.setTimeout(() => {
      setMounted(false);
      closeTimerRef.current = null;
    }, 300);
    return () => {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    };
  }, [currentOpen]);

  useEffect(() => {
    if (!mounted) return;
    const tooltip = tooltipRef.current;
    const canvas = canvasRef.current;
    if (!tooltip || !canvas) return;
    let renderer: LiquidGlassRenderer;
    try {
      renderer = new LiquidGlassRenderer(canvas, backgroundImage, mergedSettings);
      renderer.setBackgroundSampling(true);
      renderer.setTrack(-1000, -900, -1000, -950);
      rendererRef.current = renderer;
    } catch (error) {
      console.warn(error);
      return;
    }
    const resize = (width: number, height: number) => {
      if (!width || !height) return;
      sizeRef.current = { width, height };
      renderer.resize(width + TOOLTIP_PAD_X * 2, height + TOOLTIP_PAD_Y * 2);
      renderer.setSettings({
        ...mergedSettings,
        lensWidth: Math.max(1, width - 2),
        lensHeight: Math.max(1, height - 2),
        radius: Math.min(mergedSettings.radius, height / 2)
      });
      syncMotion(motionRef.current.stretch, currentOpen);
    };
    const observer = new ResizeObserver(([entry]) => resize(entry.contentRect.width, entry.contentRect.height));
    observer.observe(tooltip);
    resize(tooltip.clientWidth, tooltip.clientHeight);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frameRef.current);
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [backgroundImage, mergedSettings, mounted]);

  return (
    <span
      className={`lg-tooltip ${className}`}
      onPointerEnter={() => commitOpen(true)}
      onPointerLeave={() => commitOpen(false)}
      onFocus={() => commitOpen(true)}
      onBlur={() => commitOpen(false)}
    >
      <span className="lg-tooltip__trigger" aria-describedby={currentOpen ? id : undefined}>{children}</span>
      {mounted ? (
        <span
          ref={tooltipRef}
          id={id}
          role="tooltip"
          className={`lg-tooltip__bubble lg-tooltip__bubble--${placement} ${currentOpen ? "is-open" : ""}`}
        >
          <canvas ref={canvasRef} className="lg-tooltip__glass" aria-hidden="true" />
          <span className="lg-tooltip__content">{content}</span>
        </span>
      ) : null}
    </span>
  );
}
