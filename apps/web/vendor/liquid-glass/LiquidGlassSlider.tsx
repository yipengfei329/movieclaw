import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const MATERIAL_HANDOFF = 0.32;
const MORPH_SPRING_IN = 0.12;
const MORPH_SPRING_OUT = 0.145;
const MORPH_DAMPING_IN = 0.72;
const MORPH_DAMPING_OUT = 0.68;
const COMPACT_RELEASE_HANDOFF = MATERIAL_HANDOFF + 0.1;

export interface LiquidGlassSliderProps {
  value?: number;
  defaultValue?: number;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  backgroundImage: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  compact?: boolean;
  showIcons?: boolean;
  onValueChange?: (value: number) => void;
  className?: string;
  "aria-label"?: string;
}

export function LiquidGlassSlider({
  value,
  defaultValue = 4,
  min = 0,
  max = 100,
  step = 1,
  disabled = false,
  backgroundImage,
  variant = "clear",
  settings,
  compact = false,
  showIcons = true,
  onValueChange,
  className = "",
  "aria-label": ariaLabel = "Liquid glass slider"
}: LiquidGlassSliderProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const resizeRef = useRef({ width: 0, height: 0 });
  const pointerRef = useRef({
    active: false,
    releasing: false,
    releaseRequested: false,
    releaseBounces: 0,
    previousDirection: 0,
    lastX: 0,
    lastTime: 0,
    stretch: 0,
    velocity: 0,
    target: 0,
    morph: 0,
    morphVelocity: 0,
    morphTarget: 0
  });
  const [internalValue, setInternalValue] = useState(defaultValue);
  const [active, setActive] = useState(false);
  const currentValue = value ?? internalValue;
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, settings), [variant, settings]);
  const valueRange = Math.max(Number.EPSILON, max - min);
  const ratio = Math.max(0, Math.min(1, (currentValue - min) / valueRange));
  const restingHalfWidth = 17;
  const trackInset = compact ? 20 : 44;
  const visualInset = trackInset + restingHalfWidth;

  const ensureRenderer = () => {
    if (rendererRef.current || !canvasRef.current) return;
    try {
      rendererRef.current = new LiquidGlassRenderer(canvasRef.current, backgroundImage, mergedSettings);
      const { width, height } = resizeRef.current;
      if (width && height) rendererRef.current.resize(width, height);
    } catch (error) {
      console.warn(error);
    }
  };

  const releaseCompactRenderer = () => {
    if (!compact || !rendererRef.current) return;
    rendererRef.current.dispose();
    rendererRef.current = null;
  };

  const commit = (next: number) => {
    const stepped = Math.round((next - min) / step) * step + min;
    const clamped = Math.max(min, Math.min(max, stepped));
    if (value === undefined) setInternalValue(clamped);
    onValueChange?.(clamped);
  };

  const sliderBounds = (pillWidth: number) => {
    const root = rootRef.current;
    if (!root) return { start: 0, end: 0 };
    const pillHalfWidth = pillWidth * .5;
    const start = trackInset + pillHalfWidth;
    const end = Math.max(start, root.clientWidth - trackInset - pillHalfWidth);
    return { start, end };
  };

  const thumbCenter = (morph = pointerRef.current.morph, stretch = 0) => {
    const root = rootRef.current;
    if (!root) return { x: 0, y: 0 };
    const baseWidth = 34 + (mergedSettings.lensWidth - 34) * morph;
    const pillWidth = baseWidth * (1 + Math.max(0, stretch));
    const bounds = sliderBounds(pillWidth);
    return { x: bounds.start + (bounds.end - bounds.start) * ratio, y: root.clientHeight / 2 };
  };

  const transitionState = (progress: number) => {
    const staticProgress = Math.min(1, progress / MATERIAL_HANDOFF);
    const glassProgress = Math.max(0, (progress - MATERIAL_HANDOFF) / (1 - MATERIAL_HANDOFF));
    const smoothStatic = staticProgress * staticProgress * (3 - 2 * staticProgress);
    const smoothGlass = glassProgress * glassProgress * (3 - 2 * glassProgress);
    return {
      geometryMorph: MATERIAL_HANDOFF * smoothStatic + (1 - MATERIAL_HANDOFF) * smoothGlass,
      materialMorph: smoothGlass,
      staticVisible: progress < MATERIAL_HANDOFF,
      glassVisible: progress >= MATERIAL_HANDOFF
    };
  };

  const syncRendererGeometry = (stretch: number, pressed: boolean, progress: number) => {
    const root = rootRef.current;
    if (!root) return;
    const transition = transitionState(progress);
    const center = thumbCenter(transition.geometryMorph, stretch);
    const staticWidth = 34 + (mergedSettings.lensWidth - 34) * transition.geometryMorph;
    const staticHeight = 22 + (mergedSettings.lensHeight - 22) * transition.geometryMorph;
    root.style.setProperty("--lg-thumb-center", `${center.x}px`);
    root.style.setProperty("--lg-fill-width", `${Math.max(0, center.x - trackInset)}px`);
    root.style.setProperty("--lg-static-scale-x", String(staticWidth / 34));
    root.style.setProperty("--lg-static-scale-y", String(staticHeight / 22));
    root.style.setProperty("--lg-static-opacity", transition.staticVisible ? "1" : "0");
    root.style.setProperty("--lg-glass-opacity", transition.glassVisible ? "1" : "0");
    rendererRef.current?.setTrack(trackInset, root.clientWidth - trackInset, center.y, center.x, 3.5);
    rendererRef.current?.setGeometry(
      center.x,
      center.y,
      stretch,
      pressed,
      transition.geometryMorph,
      transition.materialMorph
    );
  };

  useEffect(() => {
    if (!canvasRef.current) return;
    if (!compact) ensureRenderer();
    const root = rootRef.current;
    if (!root) return;
    const observer = new ResizeObserver(([entry]) => {
      resizeRef.current = {
        width: entry.contentRect.width,
        height: entry.contentRect.height
      };
      rendererRef.current?.resize(entry.contentRect.width, entry.contentRect.height);
      syncRendererGeometry(0, false, 0);
    });
    observer.observe(root);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frameRef.current);
      rendererRef.current?.dispose();
      rendererRef.current = null;
    };
  }, []);

  useEffect(() => { rendererRef.current?.setImage(backgroundImage); }, [backgroundImage]);
  useEffect(() => { rendererRef.current?.setSettings(mergedSettings); }, [mergedSettings]);
  useEffect(() => {
    syncRendererGeometry(pointerRef.current.stretch, active, pointerRef.current.morph);
  }, [ratio, active, mergedSettings]);

  const animate = () => {
    const p = pointerRef.current;
    p.velocity += (p.target - p.stretch) * mergedSettings.liquidSpring;
    p.velocity *= p.active
      ? mergedSettings.liquidDamping
      : Math.min(.9, mergedSettings.liquidDamping + .025);
    p.stretch = Math.max(-.022, Math.min(.24, p.stretch + p.velocity));
    p.target *= p.active ? .93 : .78;

    const morphSpring = p.morphTarget === 1 ? MORPH_SPRING_IN : MORPH_SPRING_OUT;
    const morphDamping = p.morphTarget === 1 ? MORPH_DAMPING_IN : MORPH_DAMPING_OUT;
    p.morphVelocity += (p.morphTarget - p.morph) * morphSpring;
    p.morphVelocity *= morphDamping;
    p.morph = Math.max(0, Math.min(1, p.morph + p.morphVelocity));
    if (
      (p.morph === 0 && p.morphTarget === 0 && p.morphVelocity < 0) ||
      (p.morph === 1 && p.morphTarget === 1 && p.morphVelocity > 0)
    ) {
      p.morphVelocity = 0;
    }
    if (!p.active && p.releaseRequested && p.morphTarget === 1 && p.morph >= .97) {
      p.morphTarget = 0;
    }
    if (
      compact &&
      !p.active &&
      p.releaseRequested &&
      p.morphTarget > 0 &&
      p.morphTarget < 1 &&
      p.morph >= p.morphTarget - .01
    ) {
      p.morphTarget = 0;
    }

    if (p.releasing) {
      const direction = Math.sign(p.stretch);
      if (direction && p.previousDirection && direction !== p.previousDirection) {
        p.releaseBounces += 1;
      }
      if (direction) p.previousDirection = direction;
      if (p.releaseBounces >= 2) {
        p.stretch *= .72;
        p.velocity *= .48;
      }
    }

    syncRendererGeometry(p.stretch, p.active, p.morph);
    const shapeSettled = Math.abs(p.morphTarget - p.morph) + Math.abs(p.morphVelocity) < .002;
    if (!p.active && p.releaseBounces >= 2 && Math.abs(p.stretch) + Math.abs(p.velocity) < .006 && shapeSettled) {
      p.stretch = 0;
      p.velocity = 0;
      p.target = 0;
      p.morph = 0;
      p.morphVelocity = 0;
      p.releasing = false;
      p.releaseRequested = false;
      syncRendererGeometry(0, false, 0);
      releaseCompactRenderer();
      return;
    }
    if (
      Math.abs(p.target) + Math.abs(p.stretch) + Math.abs(p.velocity) > .0015 ||
      !shapeSettled
    ) {
      frameRef.current = requestAnimationFrame(animate);
    } else {
      p.stretch = 0;
      p.velocity = 0;
      p.target = 0;
      p.releasing = false;
      if (p.morph === 0) p.releaseRequested = false;
      p.morph = p.morphTarget;
      p.morphVelocity = 0;
      syncRendererGeometry(0, p.active, p.morph);
      if (!p.active && p.morph === 0) releaseCompactRenderer();
    }
  };

  const updateFromPointer = (clientX: number) => {
    const root = rootRef.current;
    if (!root) return;
    const rect = root.getBoundingClientRect();
    const bounds = sliderBounds(mergedSettings.lensWidth);
    const ratio = Math.max(
      0,
      Math.min(
        1,
        (clientX - rect.left - bounds.start) /
          Math.max(Number.EPSILON, bounds.end - bounds.start)
      )
    );
    commit(min + ratio * (max - min));
    const now = performance.now();
    const elapsed = Math.max(8, now - pointerRef.current.lastTime);
    const speed = Math.abs(clientX - pointerRef.current.lastX) / elapsed * 1000;
    pointerRef.current.lastX = clientX;
    pointerRef.current.lastTime = now;
    pointerRef.current.target = Math.max(
      pointerRef.current.target * .72,
      .035 + Math.min(1, speed / 1600) * mergedSettings.liquidMotion
    );
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(animate);
  };

  return (
    <div
      ref={rootRef}
      className={`lg-slider ${compact ? "is-compact" : ""} ${active ? "is-active" : ""} ${disabled ? "is-disabled" : ""} ${className}`}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={ariaLabel}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={currentValue}
      onKeyDown={(event) => {
        if (disabled) return;
        if (event.key === "ArrowLeft" || event.key === "ArrowDown") { event.preventDefault(); commit(currentValue - step); }
        if (event.key === "ArrowRight" || event.key === "ArrowUp") { event.preventDefault(); commit(currentValue + step); }
        if (event.key === "Home") { event.preventDefault(); commit(min); }
        if (event.key === "End") { event.preventDefault(); commit(max); }
      }}
      onPointerDown={(event) => {
        if (disabled) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        pointerRef.current.active = true;
        pointerRef.current.releasing = false;
        pointerRef.current.releaseRequested = false;
        pointerRef.current.releaseBounces = 0;
        pointerRef.current.previousDirection = Math.sign(pointerRef.current.stretch);
        pointerRef.current.lastX = event.clientX;
        pointerRef.current.lastTime = performance.now();
        pointerRef.current.morphTarget = 1;
        ensureRenderer();
        setActive(true);
        updateFromPointer(event.clientX);
      }}
      onPointerEnter={() => {
        if (compact && !disabled) ensureRenderer();
      }}
      onPointerLeave={() => {
        if (compact && !pointerRef.current.active) releaseCompactRenderer();
      }}
      onFocus={() => {
        if (compact && !disabled) ensureRenderer();
      }}
      onBlur={() => {
        if (compact && !pointerRef.current.active) releaseCompactRenderer();
      }}
      onPointerMove={(event) => { if (pointerRef.current.active) updateFromPointer(event.clientX); }}
      onPointerUp={(event) => {
        pointerRef.current.active = false;
        pointerRef.current.releasing = true;
        pointerRef.current.releaseRequested = true;
        pointerRef.current.releaseBounces = 0;
        pointerRef.current.previousDirection = Math.sign(pointerRef.current.stretch);
        pointerRef.current.target = 0;
        pointerRef.current.morphTarget = compact
          ? pointerRef.current.morph >= MATERIAL_HANDOFF
            ? 0
            : COMPACT_RELEASE_HANDOFF
          : pointerRef.current.morph >= .82
            ? 0
            : 1;
        event.currentTarget.releasePointerCapture(event.pointerId);
        setActive(false);
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animate);
      }}
      onPointerCancel={() => {
        pointerRef.current.active = false;
        pointerRef.current.releasing = true;
        pointerRef.current.releaseRequested = true;
        pointerRef.current.target = 0;
        pointerRef.current.morphTarget = compact
          ? pointerRef.current.morph >= MATERIAL_HANDOFF
            ? 0
            : COMPACT_RELEASE_HANDOFF
          : pointerRef.current.morph >= .82
            ? 0
            : 1;
        setActive(false);
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animate);
      }}
    >
      <canvas ref={canvasRef} className="lg-slider__glass" aria-hidden="true" />
      {showIcons && <span className="lg-slider__icon lg-slider__icon--start" aria-hidden="true"><i /><i /></span>}
      <span className="lg-slider__track" aria-hidden="true">
        <span className="lg-slider__fill" />
        <span className="lg-slider__tick lg-slider__tick--one" />
        <span className="lg-slider__tick lg-slider__tick--two" />
      </span>
      <span
        className="lg-slider__thumb"
        style={{ left: `var(--lg-thumb-center, calc(${visualInset}px + (100% - ${visualInset * 2}px) * ${ratio}))` }}
        aria-hidden="true"
      />
      {showIcons && <span className="lg-slider__icon lg-slider__icon--end" aria-hidden="true"><i /><i /></span>}
    </div>
  );
}
