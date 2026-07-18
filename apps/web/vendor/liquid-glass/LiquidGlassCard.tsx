import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const CARD_RENDER_PAD_X = 24;
const CARD_RENDER_PAD_Y = 12;

export interface LiquidGlassCardProps {
  backgroundImage: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  className?: string;
  children?: React.ReactNode;
  draggable?: boolean;
  position?: { x: number; y: number };
  defaultPosition?: { x: number; y: number };
  onPositionChange?: (position: { x: number; y: number }) => void;
}

export function LiquidGlassCard({
  backgroundImage,
  variant = "frosted",
  settings,
  className = "",
  children,
  draggable = false,
  position,
  defaultPosition = { x: 320, y: 240 },
  onPositionChange
}: LiquidGlassCardProps) {
  const rootRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
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
  const [internalPosition, setInternalPosition] = useState(defaultPosition);
  const currentPosition = position ?? internalPosition;
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, {
    blur: .34,
    refraction: .38,
    chromaticAberration: .025,
    distortion: .012,
    edgeHighlight: .1,
    specular: .12,
    fresnel: .9,
    depth: 32,
    darkTint: .12,
    tintStrength: .1,
    opacity: .96,
    shadow: .18,
    bevel: 0,
    ...settings
  }), [settings, variant]);

  const setCardPosition = (next: { x: number; y: number }) => {
    if (position === undefined) setInternalPosition(next);
    onPositionChange?.(next);
  };

  const syncMotion = (stretch: number, pressed: boolean) => {
    const { width, height } = sizeRef.current;
    if (!width || !height) return;
    rendererRef.current?.setGeometry(
      width / 2 + CARD_RENDER_PAD_X,
      height / 2 + CARD_RENDER_PAD_Y,
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
      renderer.resize(width + CARD_RENDER_PAD_X * 2, height + CARD_RENDER_PAD_Y * 2);
      renderer.setSettings({
        ...mergedSettings,
        lensWidth: Math.max(1, width - 4),
        lensHeight: Math.max(1, height - 4),
        radius: Math.min(mergedSettings.radius, height / 2)
      });
      renderer.setGeometry(
        width / 2 + CARD_RENDER_PAD_X,
        height / 2 + CARD_RENDER_PAD_Y,
        dragRef.current.stretch,
        dragRef.current.active,
        1,
        1,
        0
      );
    };

    const observer = new ResizeObserver(([entry]) => {
      resize(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(root);
    resize(root.clientWidth, root.clientHeight);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(frameRef.current);
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [backgroundImage, mergedSettings]);

  return (
    <section
      ref={rootRef}
      className={`lg-card ${draggable ? "is-draggable" : ""} ${dragRef.current.active ? "is-dragging" : ""} ${className}`}
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
        setCardPosition({
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
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          event.currentTarget.releasePointerCapture(event.pointerId);
        }
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
      <canvas ref={canvasRef} className="lg-card__glass" aria-hidden="true" />
      <div className="lg-card__content">{children}</div>
    </section>
  );
}
