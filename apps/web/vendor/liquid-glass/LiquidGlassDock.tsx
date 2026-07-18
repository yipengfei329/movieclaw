import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const INDICATOR_POSITION_SPRING = .032;
const INDICATOR_POSITION_DAMPING = .72;
const INDICATOR_SHAPE_SPRING = .06;
const INDICATOR_SHAPE_DAMPING = .62;
const INDICATOR_MORPH_EASE = .22;
const INDICATOR_STATIC_MORPH = .72;

export interface LiquidGlassDockItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
  badge?: string;
}

export interface LiquidGlassDockProps {
  backgroundImage: string;
  items?: LiquidGlassDockItem[];
  value?: string;
  defaultValue?: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  draggable?: boolean;
  position?: { x: number; y: number };
  defaultPosition?: { x: number; y: number };
  onPositionChange?: (position: { x: number; y: number }) => void;
  onValueChange?: (value: string) => void;
  onSearch?: () => void;
  className?: string;
  "aria-label"?: string;
}

const defaultItems: LiquidGlassDockItem[] = [
  { id: "home", label: "Home" },
  { id: "dashboard", label: "Dashboard" },
  { id: "analytics", label: "Analytics" },
  { id: "settings", label: "Settings" }
];

function DockIcon({ id }: { id: string }) {
  if (id === "dashboard") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>;
  }
  if (id === "analytics") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V10M10 20V4M16 20v-7M22 20V7" /></svg>;
  }
  if (id === "settings") {
    return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 0 1-4 0v-.09A1.7 1.7 0 0 0 8.97 19.35a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 0 1 0-4h.09A1.7 1.7 0 0 0 4.65 8.95a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9.02 4.6 1.7 1.7 0 0 0 10.05 3V3a2 2 0 0 1 4 0v.09A1.7 1.7 0 0 0 15.08 4.65a1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 0 1 0 4h-.09A1.7 1.7 0 0 0 19.4 15Z" /></svg>;
  }
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 11 9-8 9 8v10h-6v-6H9v6H3V11Z" /></svg>;
}

function SearchIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>;
}

export function LiquidGlassDock({
  backgroundImage,
  items = defaultItems,
  value,
  defaultValue,
  variant = "dark",
  settings,
  draggable = false,
  position,
  defaultPosition = { x: 280, y: 560 },
  onPositionChange,
  onValueChange,
  onSearch,
  className = "",
  "aria-label": ariaLabel = "Primary navigation"
}: LiquidGlassDockProps) {
  const mainCanvasRef = useRef<HTMLCanvasElement>(null);
  const indicatorCanvasRef = useRef<HTMLCanvasElement>(null);
  const searchCanvasRef = useRef<HTMLCanvasElement>(null);
  const mainRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const indicatorRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const searchRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const indicatorFrameRef = useRef(0);
  const indicatorFadeTimeoutRef = useRef<number | null>(null);
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
  const indicatorRef = useRef({
    active: false,
    moved: false,
    x: 0,
    targetX: 0,
    velocityX: 0,
    stretch: 0,
    targetStretch: 0,
    stretchVelocity: 0,
    morph: 1,
    targetMorph: 1,
    morphVelocity: 0,
    glassReleased: true,
    lastX: 0,
    lastTime: 0
  });
  const initialValue = defaultValue ?? items[0]?.id ?? "";
  const [internalValue, setInternalValue] = useState(initialValue);
  const [internalPosition, setInternalPosition] = useState(defaultPosition);
  const [indicatorGlassVisible, setIndicatorGlassVisible] = useState(false);
  const [indicatorStaticHidden, setIndicatorStaticHidden] = useState(false);
  const selectedValue = value ?? internalValue;
  const selectedIndex = Math.max(0, items.findIndex((item) => item.id === selectedValue));
  const selectedIndexRef = useRef(selectedIndex);
  selectedIndexRef.current = selectedIndex;
  const currentPosition = position ?? internalPosition;
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, {
    ...settings,
    blur: .28,
    refraction: .46,
    chromaticAberration: .035,
    distortion: .015,
    edgeHighlight: .12,
    specular: .15,
    fresnel: 1.02,
    depth: 40,
    brightness: -.1,
    saturation: -.12,
    darkTint: .46,
    tintStrength: .16,
    opacity: 1,
    shadow: 0,
    bevel: 0
  }), [settings, variant]);

  const syncMotion = (stretch: number, pressed: boolean) => {
    mainRendererRef.current?.setGeometry(214, 50, stretch, pressed, 1, 1, 0);
    searchRendererRef.current?.setGeometry(46, 46, stretch * .55, pressed, 1, 1, 0);
  };

  const animateMotion = () => {
    const drag = dragRef.current;
    drag.velocity += (drag.target - drag.stretch) * mergedSettings.liquidSpring;
    drag.velocity *= mergedSettings.liquidDamping;
    drag.stretch = Math.max(-.025, Math.min(.08, drag.stretch + drag.velocity));
    drag.target *= drag.active ? .9 : .7;
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

  const itemCenter = (index: number) => {
    const cellWidth = 412 / Math.max(1, items.length);
    return 8 + cellWidth * (index + .5);
  };

  const syncIndicator = () => {
    const indicator = indicatorRef.current;
    indicatorRendererRef.current?.setGeometry(
      indicator.x,
      50,
      indicator.stretch,
      indicator.active,
      indicator.morph,
      1,
      0
    );
  };

  const animateIndicator = () => {
    const indicator = indicatorRef.current;
    if (!indicator.active) {
      const previousError = indicator.targetX - indicator.x;
      indicator.velocityX += previousError * INDICATOR_POSITION_SPRING;
      indicator.velocityX *= INDICATOR_POSITION_DAMPING;
      const nextX = indicator.x + indicator.velocityX;
      if ((indicator.targetX - nextX) * previousError <= 0) {
        indicator.x = indicator.targetX;
        indicator.velocityX = 0;
      } else {
        indicator.x = nextX;
      }
    }
    indicator.stretchVelocity += (indicator.targetStretch - indicator.stretch) * INDICATOR_SHAPE_SPRING;
    indicator.stretchVelocity *= INDICATOR_SHAPE_DAMPING;
    indicator.stretch = Math.max(0, Math.min(.16, indicator.stretch + indicator.stretchVelocity));
    if (indicator.stretch === 0 && indicator.stretchVelocity < 0) indicator.stretchVelocity = 0;
    indicator.targetStretch *= indicator.active ? .9 : .54;
    const morphDelta = indicator.targetMorph - indicator.morph;
    indicator.morph = Math.max(
      INDICATOR_STATIC_MORPH,
      Math.min(1, Math.abs(morphDelta) < .004 ? indicator.targetMorph : indicator.morph + morphDelta * INDICATOR_MORPH_EASE)
    );
    indicator.morphVelocity = 0;
    syncIndicator();
    const positionSettled = indicator.active || Math.abs(indicator.targetX - indicator.x) + Math.abs(indicator.velocityX) < .02;
    const shapeSettled = Math.abs(indicator.targetStretch) + Math.abs(indicator.stretch) + Math.abs(indicator.stretchVelocity) < .002;
    const morphSettled = Math.abs(indicator.targetMorph - indicator.morph) < .006;
    const materialAtRest = Math.abs(indicator.targetX - indicator.x) + Math.abs(indicator.velocityX) < .45;
    if (!indicator.active && !indicator.glassReleased && materialAtRest) {
      indicator.targetMorph = INDICATOR_STATIC_MORPH;
    }
    if (!indicator.active && !indicator.glassReleased && materialAtRest && morphSettled) {
      indicator.glassReleased = true;
      setIndicatorStaticHidden(false);
      if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
      indicatorFadeTimeoutRef.current = window.setTimeout(() => {
        setIndicatorGlassVisible(false);
        indicatorFadeTimeoutRef.current = null;
      }, 120);
    }
    if (!positionSettled || !shapeSettled || !morphSettled) {
      indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
    } else if (!indicator.active) {
      indicator.x = indicator.targetX;
      indicator.velocityX = 0;
      indicator.stretch = 0;
      indicator.targetStretch = 0;
      indicator.stretchVelocity = 0;
      indicator.morph = indicator.targetMorph;
      indicator.morphVelocity = 0;
      syncIndicator();
    }
  };

  const moveIndicatorTo = (index: number, settleToStatic = true) => {
    const indicator = indicatorRef.current;
    indicator.targetX = itemCenter(index);
    indicator.targetMorph = settleToStatic ? INDICATOR_STATIC_MORPH : 1;
    if (Math.abs(indicator.targetX - indicator.x) > 1) {
      indicator.targetStretch = Math.max(indicator.targetStretch, .075);
    }
    cancelAnimationFrame(indicatorFrameRef.current);
    indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
  };

  useEffect(() => {
    const mainCanvas = mainCanvasRef.current;
    const indicatorCanvas = indicatorCanvasRef.current;
    const searchCanvas = searchCanvasRef.current;
    if (!mainCanvas || !indicatorCanvas || !searchCanvas) return;
    try {
      const mainSettings = { ...mergedSettings, lensWidth: 416, lensHeight: 88, radius: 44 };
      const searchSettings = { ...mergedSettings, lensWidth: 84, lensHeight: 84, radius: 42 };
      const indicatorSettings = {
        ...mergedSettings,
        lensWidth: 412 / Math.max(1, items.length) + 18,
        lensHeight: 92,
        radius: 46,
        brightness: -.03,
        darkTint: .24,
        edgeHighlight: .16,
        specular: .18,
        shadow: 0
      };
      const mainRenderer = new LiquidGlassRenderer(mainCanvas, backgroundImage, mainSettings);
      const indicatorRenderer = new LiquidGlassRenderer(indicatorCanvas, backgroundImage, indicatorSettings);
      const searchRenderer = new LiquidGlassRenderer(searchCanvas, backgroundImage, searchSettings);
      mainRenderer.setBackgroundSampling(true);
      indicatorRenderer.setBackgroundSampling(true);
      searchRenderer.setBackgroundSampling(true);
      mainRenderer.resize(428, 100);
      indicatorRenderer.resize(428, 100);
      searchRenderer.resize(92, 92);
      mainRenderer.setSettings(mainSettings);
      indicatorRenderer.setSettings(indicatorSettings);
      searchRenderer.setSettings(searchSettings);
      mainRenderer.setTrack(-1000, -900, -1000, -950);
      indicatorRenderer.setTrack(-1000, -900, -1000, -950);
      searchRenderer.setTrack(-1000, -900, -1000, -950);
      mainRendererRef.current = mainRenderer;
      indicatorRendererRef.current = indicatorRenderer;
      searchRendererRef.current = searchRenderer;
      const initialIndicatorX = itemCenter(selectedIndexRef.current);
      indicatorRef.current.x = initialIndicatorX;
      indicatorRef.current.targetX = initialIndicatorX;
      indicatorRef.current.morph = INDICATOR_STATIC_MORPH;
      indicatorRef.current.targetMorph = INDICATOR_STATIC_MORPH;
      syncMotion(0, false);
      syncIndicator();
    } catch (error) {
      console.warn(error);
    }
    return () => {
      if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
      cancelAnimationFrame(frameRef.current);
      cancelAnimationFrame(indicatorFrameRef.current);
      mainRendererRef.current?.dispose();
      indicatorRendererRef.current?.dispose();
      searchRendererRef.current?.dispose();
      mainRendererRef.current = null;
      indicatorRendererRef.current = null;
      searchRendererRef.current = null;
    };
  }, [backgroundImage, items.length, mergedSettings]);

  useEffect(() => {
    if (indicatorRef.current.active || !indicatorRef.current.glassReleased) return;
    moveIndicatorTo(selectedIndex, true);
  }, [selectedIndex]);

  const commitValue = (next: string) => {
    if (value === undefined) setInternalValue(next);
    onValueChange?.(next);
  };

  return (
    <div
      className={`lg-dock ${draggable ? "is-draggable" : ""} ${className}`}
      data-indicator-moving={indicatorStaticHidden ? "true" : "false"}
      style={draggable ? { transform: `translate3d(${currentPosition.x}px, ${currentPosition.y}px, 0)` } : undefined}
      onClickCapture={(event) => {
        if (!dragRef.current.moved) return;
        event.preventDefault();
        event.stopPropagation();
        dragRef.current.moved = false;
      }}
      onPointerDown={(event) => {
        if (!draggable) return;
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
      }}
      onPointerMove={(event) => {
        const drag = dragRef.current;
        if (!draggable || !drag.active) return;
        if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) <= 3 && !drag.moved) return;
        if (!drag.moved) {
          drag.moved = true;
          event.currentTarget.setPointerCapture(event.pointerId);
        }
        const next = { x: event.clientX - drag.offsetX, y: event.clientY - drag.offsetY };
        if (position === undefined) setInternalPosition(next);
        onPositionChange?.(next);
        const now = performance.now();
        const elapsed = Math.max(8, now - drag.lastTime);
        const speed = Math.hypot(event.clientX - drag.lastX, event.clientY - drag.lastY) / elapsed * 1000;
        drag.lastX = event.clientX;
        drag.lastY = event.clientY;
        drag.lastTime = now;
        drag.target = Math.min(.08, .014 + speed / 8500 * mergedSettings.liquidMotion * 4);
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateMotion);
      }}
      onPointerUp={(event) => {
        const drag = dragRef.current;
        if (!draggable || !drag.active) return;
        drag.active = false;
        drag.target = 0;
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
      <nav className="lg-dock__main" aria-label={ariaLabel}>
        <canvas ref={mainCanvasRef} className="lg-dock__glass" aria-hidden="true" />
        <canvas
          ref={indicatorCanvasRef}
          className={`lg-dock__indicator-glass ${indicatorGlassVisible ? "is-visible" : ""}`}
          aria-hidden="true"
        />
        <div className="lg-dock__items">
          {items.map((item, index) => (
            <button
              type="button"
              key={item.id}
              className={`lg-dock__item ${selectedValue === item.id ? "is-selected" : ""}`}
              aria-current={selectedValue === item.id ? "page" : undefined}
              onClick={() => {
                if (indicatorRef.current.moved) {
                  indicatorRef.current.moved = false;
                  return;
                }
                if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
                setIndicatorGlassVisible(true);
                setIndicatorStaticHidden(true);
                indicatorRef.current.glassReleased = false;
                indicatorRef.current.morph = INDICATOR_STATIC_MORPH;
                indicatorRef.current.targetMorph = 1;
                indicatorRef.current.morphVelocity = 0;
                commitValue(item.id);
                moveIndicatorTo(index, false);
              }}
              onPointerDown={(event) => {
                if (selectedValue !== item.id) return;
                event.stopPropagation();
                event.currentTarget.setPointerCapture(event.pointerId);
                const indicator = indicatorRef.current;
                if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
                setIndicatorGlassVisible(true);
                setIndicatorStaticHidden(true);
                indicator.glassReleased = false;
                indicator.active = true;
                indicator.moved = false;
                indicator.morph = INDICATOR_STATIC_MORPH;
                indicator.targetMorph = 1;
                indicator.morphVelocity = 0;
                indicator.lastX = event.clientX;
                indicator.lastTime = performance.now();
                indicator.velocityX = 0;
                indicator.targetStretch = .035;
                cancelAnimationFrame(indicatorFrameRef.current);
                indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
              }}
              onPointerMove={(event) => {
                const indicator = indicatorRef.current;
                if (!indicator.active || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
                const rect = mainCanvasRef.current?.getBoundingClientRect();
                if (!rect) return;
                const minX = itemCenter(0);
                const maxX = itemCenter(items.length - 1);
                indicator.x = Math.max(minX, Math.min(maxX, event.clientX - rect.left));
                if (Math.abs(event.clientX - indicator.lastX) > 2) indicator.moved = true;
                const now = performance.now();
                const elapsed = Math.max(8, now - indicator.lastTime);
                const speed = Math.abs(event.clientX - indicator.lastX) / elapsed * 1000;
                indicator.lastX = event.clientX;
                indicator.lastTime = now;
                indicator.targetStretch = Math.min(.16, .035 + speed / 2200 * mergedSettings.liquidMotion);
                const cellWidth = 412 / Math.max(1, items.length);
                const nextIndex = Math.max(0, Math.min(items.length - 1, Math.round((indicator.x - 8 - cellWidth * .5) / cellWidth)));
                if (nextIndex !== selectedIndexRef.current) {
                  selectedIndexRef.current = nextIndex;
                  commitValue(items[nextIndex].id);
                }
                syncIndicator();
                cancelAnimationFrame(indicatorFrameRef.current);
                indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
              }}
              onPointerUp={(event) => {
                const indicator = indicatorRef.current;
                if (!indicator.active) return;
                indicator.active = false;
                indicator.targetX = itemCenter(selectedIndexRef.current);
                indicator.targetStretch = 0;
                indicator.targetMorph = INDICATOR_STATIC_MORPH;
                if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
                cancelAnimationFrame(indicatorFrameRef.current);
                indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
              }}
              onPointerCancel={() => {
                const indicator = indicatorRef.current;
                indicator.active = false;
                indicator.moved = false;
                indicator.targetX = itemCenter(selectedIndexRef.current);
                indicator.targetStretch = 0;
                indicator.targetMorph = INDICATOR_STATIC_MORPH;
                cancelAnimationFrame(indicatorFrameRef.current);
                indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
              }}
            >
              <span className="lg-dock__icon">{item.icon ?? <DockIcon id={item.id} />}</span>
              <span className="lg-dock__label">{item.label}</span>
              {item.badge ? <span className="lg-dock__badge">{item.badge}</span> : null}
            </button>
          ))}
        </div>
      </nav>
      <button type="button" className="lg-dock__search" aria-label="Search" onClick={onSearch}>
        <canvas ref={searchCanvasRef} className="lg-dock__glass" aria-hidden="true" />
        <span className="lg-dock__search-icon"><SearchIcon /></span>
      </button>
    </div>
  );
}
