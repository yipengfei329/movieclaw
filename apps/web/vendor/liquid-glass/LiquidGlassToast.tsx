import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const TOAST_PAD_X = 20;
const TOAST_PAD_Y = 12;

export interface LiquidGlassToastProps {
  backgroundImage: string;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  title?: React.ReactNode;
  children?: React.ReactNode;
  action?: React.ReactNode;
  duration?: number;
  placement?: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  draggable?: boolean;
  position?: { x: number; y: number };
  defaultPosition?: { x: number; y: number };
  onPositionChange?: (position: { x: number; y: number }) => void;
  className?: string;
  "aria-label"?: string;
}

export function LiquidGlassToast({
  backgroundImage,
  open,
  defaultOpen = false,
  onOpenChange,
  title,
  children,
  action,
  duration = 4000,
  placement = "bottom-right",
  variant = "dark",
  settings,
  draggable = false,
  position,
  defaultPosition = { x: 0, y: 0 },
  onPositionChange,
  className = "",
  "aria-label": ariaLabel = "Notification"
}: LiquidGlassToastProps) {
  const rootRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const closeTimerRef = useRef<number | null>(null);
  const autoTimerRef = useRef<number | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const dragRef = useRef({
    active: false,
    moved: false,
    startX: 0,
    startY: 0,
    offsetX: 0,
    offsetY: 0,
    lastX: 0,
    lastY: 0,
    lastTime: 0,
    stretch: 0,
    target: 0,
    velocity: 0
  });
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const [mounted, setMounted] = useState(open ?? defaultOpen);
  const [internalPosition, setInternalPosition] = useState(defaultPosition);
  const currentOpen = open ?? internalOpen;
  const currentPosition = position ?? internalPosition;
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, {
    blur: .3,
    refraction: .38,
    chromaticAberration: .03,
    distortion: .014,
    edgeHighlight: .12,
    specular: .16,
    fresnel: 1,
    depth: 34,
    brightness: -.08,
    saturation: -.08,
    darkTint: .42,
    tintStrength: .14,
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
      width / 2 + TOAST_PAD_X,
      height / 2 + TOAST_PAD_Y,
      stretch,
      pressed,
      1,
      1,
      0
    );
  };

  const animateMotion = () => {
    const drag = dragRef.current;
    drag.velocity += (drag.target - drag.stretch) * mergedSettings.liquidSpring;
    drag.velocity *= mergedSettings.liquidDamping;
    drag.stretch = Math.max(-.025, Math.min(.1, drag.stretch + drag.velocity));
    drag.target *= drag.active ? .9 : .72;
    syncMotion(drag.stretch, drag.active);
    if (Math.abs(drag.target) + Math.abs(drag.stretch) + Math.abs(drag.velocity) > .001) {
      frameRef.current = requestAnimationFrame(animateMotion);
    } else {
      drag.target = 0;
      drag.stretch = 0;
      drag.velocity = 0;
      syncMotion(0, false);
    }
  };

  useEffect(() => {
    if (currentOpen) {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
      setMounted(true);
      if (duration > 0) {
        if (autoTimerRef.current !== null) window.clearTimeout(autoTimerRef.current);
        autoTimerRef.current = window.setTimeout(() => commitOpen(false), duration);
      }
      return;
    }
    if (autoTimerRef.current !== null) window.clearTimeout(autoTimerRef.current);
    closeTimerRef.current = window.setTimeout(() => {
      setMounted(false);
      closeTimerRef.current = null;
    }, 360);
    return () => {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
      if (autoTimerRef.current !== null) window.clearTimeout(autoTimerRef.current);
    };
  }, [currentOpen, duration]);

  useEffect(() => {
    if (!mounted) return;
    const root = rootRef.current;
    const canvas = canvasRef.current;
    if (!root || !canvas) return;
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
      renderer.resize(width + TOAST_PAD_X * 2, height + TOAST_PAD_Y * 2);
      renderer.setSettings({
        ...mergedSettings,
        lensWidth: Math.max(1, width - 4),
        lensHeight: Math.max(1, height - 4),
        radius: Math.min(mergedSettings.radius, height / 2)
      });
      syncMotion(dragRef.current.stretch, dragRef.current.active);
    };

    const observer = new ResizeObserver(([entry]) => resize(entry.contentRect.width, entry.contentRect.height));
    observer.observe(root);
    resize(root.clientWidth, root.clientHeight);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frameRef.current);
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [backgroundImage, mergedSettings, mounted]);

  const setToastPosition = (next: { x: number; y: number }) => {
    if (position === undefined) setInternalPosition(next);
    onPositionChange?.(next);
  };

  if (!mounted) return null;

  return (
    <section
      ref={rootRef}
      role="status"
      aria-label={ariaLabel}
      className={`lg-toast lg-toast--${placement} ${currentOpen ? "is-open" : ""} ${draggable ? "is-draggable" : ""} ${className}`}
      style={draggable ? { transform: `translate3d(${currentPosition.x}px, ${currentPosition.y}px, 0)` } : undefined}
      onClickCapture={(event) => {
        if (!dragRef.current.moved) return;
        event.preventDefault();
        event.stopPropagation();
        dragRef.current.moved = false;
      }}
      onPointerDown={(event) => {
        if (!draggable) return;
        if ((event.target as HTMLElement).closest("button, a, input, select, textarea")) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        const drag = dragRef.current;
        drag.active = true;
        drag.moved = false;
        drag.startX = event.clientX;
        drag.startY = event.clientY;
        drag.offsetX = event.clientX - currentPosition.x;
        drag.offsetY = event.clientY - currentPosition.y;
        drag.lastX = event.clientX;
        drag.lastY = event.clientY;
        drag.lastTime = performance.now();
        drag.target = .025;
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateMotion);
      }}
      onPointerMove={(event) => {
        const drag = dragRef.current;
        if (!draggable || !drag.active || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
        const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
        if (distance > 3) drag.moved = true;
        if (!drag.moved) return;
        setToastPosition({
          x: event.clientX - drag.offsetX,
          y: event.clientY - drag.offsetY
        });
        const now = performance.now();
        const elapsed = Math.max(8, now - drag.lastTime);
        const speed = Math.hypot(event.clientX - drag.lastX, event.clientY - drag.lastY) / elapsed * 1000;
        drag.lastX = event.clientX;
        drag.lastY = event.clientY;
        drag.lastTime = now;
        drag.target = Math.min(.1, .018 + speed / 9000 * mergedSettings.liquidMotion * 4);
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateMotion);
      }}
      onPointerUp={(event) => {
        if (!draggable) return;
        dragRef.current.active = false;
        dragRef.current.target = 0;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateMotion);
      }}
      onPointerCancel={() => {
        dragRef.current.active = false;
        dragRef.current.moved = false;
        dragRef.current.target = 0;
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateMotion);
      }}
    >
      <canvas ref={canvasRef} className="lg-toast__glass" aria-hidden="true" />
      <div className="lg-toast__content">
        <div className="lg-toast__copy">
          {title ? <strong>{title}</strong> : null}
          {children ? <span>{children}</span> : null}
        </div>
        {action ? <div className="lg-toast__action">{action}</div> : null}
        <button type="button" className="lg-toast__close" aria-label="Dismiss notification" onClick={() => commitOpen(false)}>×</button>
      </div>
    </section>
  );
}
