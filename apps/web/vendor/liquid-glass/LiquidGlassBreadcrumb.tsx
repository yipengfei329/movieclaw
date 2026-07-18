import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const CRUMB_POSITION_SPRING = .035;
const CRUMB_POSITION_DAMPING = .72;
const CRUMB_SHAPE_SPRING = .06;
const CRUMB_SHAPE_DAMPING = .62;
const CRUMB_MORPH_EASE = .22;
const CRUMB_STATIC_MORPH = .72;
const CRUMB_WIDTH = 540;
const CRUMB_HEIGHT = 64;

export interface LiquidGlassBreadcrumbItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
}

export interface LiquidGlassBreadcrumbProps {
  backgroundImage: string;
  items?: LiquidGlassBreadcrumbItem[];
  value?: string;
  defaultValue?: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  draggable?: boolean;
  position?: { x: number; y: number };
  defaultPosition?: { x: number; y: number };
  onPositionChange?: (position: { x: number; y: number }) => void;
  onValueChange?: (value: string) => void;
  className?: string;
  "aria-label"?: string;
}

const defaultItems: LiquidGlassBreadcrumbItem[] = [
  { id: "home", label: "Home" },
  { id: "library", label: "Library" },
  { id: "components", label: "Components" },
  { id: "breadcrumb", label: "Breadcrumb" }
];

function Chevron() {
  return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" /></svg>;
}

export function LiquidGlassBreadcrumb({
  backgroundImage,
  items = defaultItems,
  value,
  defaultValue,
  variant = "dark",
  settings,
  draggable = false,
  position,
  defaultPosition = { x: 320, y: 300 },
  onPositionChange,
  onValueChange,
  className = "",
  "aria-label": ariaLabel = "Breadcrumb"
}: LiquidGlassBreadcrumbProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const baseCanvasRef = useRef<HTMLCanvasElement>(null);
  const indicatorCanvasRef = useRef<HTMLCanvasElement>(null);
  const baseRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const indicatorRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const indicatorFrameRef = useRef(0);
  const indicatorFadeTimeoutRef = useRef<number | null>(null);
  const measurementsRef = useRef<Array<{ x: number; width: number }>>([]);
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
    x: 0,
    targetX: 0,
    velocityX: 0,
    stretch: 0,
    targetStretch: 0,
    stretchVelocity: 0,
    morph: CRUMB_STATIC_MORPH,
    targetMorph: CRUMB_STATIC_MORPH,
    glassReleased: true
  });
  const initialValue = defaultValue ?? items[items.length - 1]?.id ?? items[0]?.id ?? "";
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
    blur: .24,
    refraction: .34,
    chromaticAberration: .03,
    distortion: .012,
    edgeHighlight: .12,
    specular: .16,
    fresnel: 1,
    depth: 32,
    brightness: -.08,
    saturation: -.08,
    darkTint: .42,
    tintStrength: .14,
    opacity: 1,
    shadow: 0,
    bevel: 0
  }), [settings, variant]);

  const measureItems = () => {
    const root = rootRef.current;
    if (!root) return measurementsRef.current;
    const rootRect = root.getBoundingClientRect();
    measurementsRef.current = Array.from(root.querySelectorAll<HTMLButtonElement>(".lg-breadcrumb__item")).map((item) => {
      const rect = item.getBoundingClientRect();
      return { x: rect.left - rootRect.left + rect.width / 2, width: rect.width };
    });
    return measurementsRef.current;
  };

  const itemCenter = (index: number) => {
    const measurement = measurementsRef.current[index] ?? measureItems()[index];
    return measurement?.x ?? CRUMB_WIDTH / 2;
  };

  const itemWidth = (index: number) => {
    const measurement = measurementsRef.current[index] ?? measureItems()[index];
    return Math.max(62, measurement?.width ?? 112);
  };

  const syncIndicatorSettings = (index = selectedIndexRef.current) => {
    indicatorRendererRef.current?.setSettings({
      ...mergedSettings,
      lensWidth: itemWidth(index) + 18,
      lensHeight: 54,
      radius: 27,
      brightness: -.03,
      darkTint: .24,
      edgeHighlight: .16,
      specular: .18,
      shadow: 0
    });
  };

  const syncBase = (stretch: number, pressed: boolean) => {
    baseRendererRef.current?.setGeometry(CRUMB_WIDTH / 2, CRUMB_HEIGHT / 2, stretch, pressed, 1, 1, 0);
  };

  const animateBase = () => {
    const drag = dragRef.current;
    drag.velocity += (drag.target - drag.stretch) * mergedSettings.liquidSpring;
    drag.velocity *= mergedSettings.liquidDamping;
    drag.stretch = Math.max(-.025, Math.min(.08, drag.stretch + drag.velocity));
    drag.target *= drag.active ? .9 : .7;
    syncBase(drag.stretch, drag.active);
    if (Math.abs(drag.target) + Math.abs(drag.stretch) + Math.abs(drag.velocity) > .001) {
      frameRef.current = requestAnimationFrame(animateBase);
    } else {
      drag.target = 0;
      drag.stretch = 0;
      drag.velocity = 0;
      syncBase(0, false);
    }
  };

  const syncIndicator = () => {
    const indicator = indicatorRef.current;
    indicatorRendererRef.current?.setGeometry(indicator.x, CRUMB_HEIGHT / 2, indicator.stretch, false, indicator.morph, 1, 0);
  };

  const animateIndicator = () => {
    const indicator = indicatorRef.current;
    const previousError = indicator.targetX - indicator.x;
    indicator.velocityX += previousError * CRUMB_POSITION_SPRING;
    indicator.velocityX *= CRUMB_POSITION_DAMPING;
    const nextX = indicator.x + indicator.velocityX;
    if ((indicator.targetX - nextX) * previousError <= 0) {
      indicator.x = indicator.targetX;
      indicator.velocityX = 0;
    } else {
      indicator.x = nextX;
    }
    indicator.stretchVelocity += (indicator.targetStretch - indicator.stretch) * CRUMB_SHAPE_SPRING;
    indicator.stretchVelocity *= CRUMB_SHAPE_DAMPING;
    indicator.stretch = Math.max(0, Math.min(.16, indicator.stretch + indicator.stretchVelocity));
    if (indicator.stretch === 0 && indicator.stretchVelocity < 0) indicator.stretchVelocity = 0;
    indicator.targetStretch *= .54;
    const morphDelta = indicator.targetMorph - indicator.morph;
    indicator.morph = Math.max(
      CRUMB_STATIC_MORPH,
      Math.min(1, Math.abs(morphDelta) < .004 ? indicator.targetMorph : indicator.morph + morphDelta * CRUMB_MORPH_EASE)
    );
    syncIndicator();
    const positionSettled = Math.abs(indicator.targetX - indicator.x) + Math.abs(indicator.velocityX) < .02;
    const shapeSettled = Math.abs(indicator.targetStretch) + Math.abs(indicator.stretch) + Math.abs(indicator.stretchVelocity) < .002;
    const morphSettled = Math.abs(indicator.targetMorph - indicator.morph) < .006;
    const materialAtRest = Math.abs(indicator.targetX - indicator.x) + Math.abs(indicator.velocityX) < .45;
    if (!indicator.glassReleased && materialAtRest) indicator.targetMorph = CRUMB_STATIC_MORPH;
    if (!indicator.glassReleased && materialAtRest && morphSettled) {
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
    } else {
      indicator.x = indicator.targetX;
      indicator.velocityX = 0;
      indicator.stretch = 0;
      indicator.targetStretch = 0;
      indicator.stretchVelocity = 0;
      indicator.morph = indicator.targetMorph;
      syncIndicator();
    }
  };

  const moveIndicatorTo = (index: number, settleToStatic = true) => {
    const indicator = indicatorRef.current;
    syncIndicatorSettings(index);
    indicator.targetX = itemCenter(index);
    indicator.targetMorph = settleToStatic ? CRUMB_STATIC_MORPH : 1;
    if (Math.abs(indicator.targetX - indicator.x) > 1) {
      indicator.targetStretch = Math.max(indicator.targetStretch, .07);
    }
    cancelAnimationFrame(indicatorFrameRef.current);
    indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
  };

  useEffect(() => {
    const baseCanvas = baseCanvasRef.current;
    const indicatorCanvas = indicatorCanvasRef.current;
    if (!baseCanvas || !indicatorCanvas) return;
    try {
      const baseSettings = { ...mergedSettings, lensWidth: CRUMB_WIDTH - 4, lensHeight: CRUMB_HEIGHT - 8, radius: 28 };
      const baseRenderer = new LiquidGlassRenderer(baseCanvas, backgroundImage, baseSettings);
      const indicatorRenderer = new LiquidGlassRenderer(indicatorCanvas, backgroundImage, {
        ...mergedSettings,
        lensWidth: itemWidth(selectedIndexRef.current) + 18,
        lensHeight: 54,
        radius: 27,
        brightness: -.03,
        darkTint: .24,
        edgeHighlight: .16,
        specular: .18,
        shadow: 0
      });
      baseRenderer.setBackgroundSampling(true);
      indicatorRenderer.setBackgroundSampling(true);
      baseRenderer.resize(CRUMB_WIDTH, CRUMB_HEIGHT);
      indicatorRenderer.resize(CRUMB_WIDTH, CRUMB_HEIGHT);
      baseRenderer.setSettings(baseSettings);
      baseRenderer.setTrack(-1000, -900, -1000, -950);
      indicatorRenderer.setTrack(-1000, -900, -1000, -950);
      baseRendererRef.current = baseRenderer;
      indicatorRendererRef.current = indicatorRenderer;
      requestAnimationFrame(() => {
        measureItems();
        syncIndicatorSettings(selectedIndexRef.current);
        const initialX = itemCenter(selectedIndexRef.current);
        indicatorRef.current.x = initialX;
        indicatorRef.current.targetX = initialX;
        indicatorRef.current.morph = CRUMB_STATIC_MORPH;
        indicatorRef.current.targetMorph = CRUMB_STATIC_MORPH;
        syncBase(0, false);
        syncIndicator();
      });
    } catch (error) {
      console.warn(error);
    }
    return () => {
      if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
      cancelAnimationFrame(frameRef.current);
      cancelAnimationFrame(indicatorFrameRef.current);
      baseRendererRef.current?.dispose();
      indicatorRendererRef.current?.dispose();
      baseRendererRef.current = null;
      indicatorRendererRef.current = null;
    };
  }, [backgroundImage, items.length, mergedSettings]);

  useEffect(() => {
    if (!indicatorRef.current.glassReleased) return;
    requestAnimationFrame(() => {
      measureItems();
      moveIndicatorTo(selectedIndex, true);
    });
  }, [selectedIndex]);

  const commitValue = (next: string) => {
    if (value === undefined) setInternalValue(next);
    onValueChange?.(next);
  };

  return (
    <nav
      ref={rootRef}
      className={`lg-breadcrumb ${draggable ? "is-draggable" : ""} ${className}`}
      data-indicator-moving={indicatorStaticHidden ? "true" : "false"}
      aria-label={ariaLabel}
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
        frameRef.current = requestAnimationFrame(animateBase);
      }}
      onPointerUp={(event) => {
        const drag = dragRef.current;
        if (!draggable || !drag.active) return;
        drag.active = false;
        drag.target = 0;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateBase);
      }}
      onPointerCancel={() => {
        dragRef.current.active = false;
        dragRef.current.moved = false;
        dragRef.current.target = 0;
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateBase);
      }}
    >
      <canvas ref={baseCanvasRef} className="lg-breadcrumb__glass" aria-hidden="true" />
      <canvas
        ref={indicatorCanvasRef}
        className={`lg-breadcrumb__indicator-glass ${indicatorGlassVisible ? "is-visible" : ""}`}
        aria-hidden="true"
      />
      <ol className="lg-breadcrumb__list">
        {items.map((item, index) => (
          <li className="lg-breadcrumb__node" key={item.id}>
            <button
              type="button"
              className={`lg-breadcrumb__item ${selectedValue === item.id ? "is-selected" : ""}`}
              aria-current={selectedValue === item.id ? "page" : undefined}
              onClick={() => {
                if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
                measureItems();
                setIndicatorGlassVisible(true);
                setIndicatorStaticHidden(true);
                indicatorRef.current.glassReleased = false;
                indicatorRef.current.morph = CRUMB_STATIC_MORPH;
                indicatorRef.current.targetMorph = 1;
                commitValue(item.id);
                moveIndicatorTo(index, false);
              }}
            >
              {item.icon ? <span className="lg-breadcrumb__icon">{item.icon}</span> : null}
              <span className="lg-breadcrumb__label">{item.label}</span>
            </button>
            {index < items.length - 1 ? <span className="lg-breadcrumb__separator"><Chevron /></span> : null}
          </li>
        ))}
      </ol>
    </nav>
  );
}
