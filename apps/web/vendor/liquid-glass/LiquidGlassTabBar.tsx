import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const TAB_POSITION_SPRING = .035;
const TAB_POSITION_DAMPING = .72;
const TAB_SHAPE_SPRING = .06;
const TAB_SHAPE_DAMPING = .62;
const TAB_MORPH_EASE = .22;
const TAB_STATIC_MORPH = .72;
const BAR_WIDTH = 420;
const BAR_HEIGHT = 72;
const BAR_INSET = 8;

export interface LiquidGlassTabItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
}

export interface LiquidGlassTabBarProps {
  backgroundImage: string;
  items?: LiquidGlassTabItem[];
  value?: string;
  defaultValue?: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  /** 整条 TabBar 的宽度（px），默认 420。用于把分段拉伸到与容器/输入框等宽对齐。 */
  width?: number;
  /**
   * 是否让玻璃采样并折射背景图。默认 true。传 false 时改用恒定近黑底，
   * 得到「均匀深色」的实心观感（用于压暗、不透亮背景的场景）。
   */
  sampleBackground?: boolean;
  draggable?: boolean;
  position?: { x: number; y: number };
  defaultPosition?: { x: number; y: number };
  onPositionChange?: (position: { x: number; y: number }) => void;
  onValueChange?: (value: string) => void;
  className?: string;
  "aria-label"?: string;
}

const defaultItems: LiquidGlassTabItem[] = [
  { id: "overview", label: "Overview" },
  { id: "activity", label: "Activity" },
  { id: "reports", label: "Reports" },
  { id: "settings", label: "Settings" }
];

export function LiquidGlassTabBar({
  backgroundImage,
  items = defaultItems,
  value,
  defaultValue,
  variant = "dark",
  settings,
  width = BAR_WIDTH,
  sampleBackground = true,
  draggable = false,
  position,
  defaultPosition = { x: 340, y: 300 },
  onPositionChange,
  onValueChange,
  className = "",
  "aria-label": ariaLabel = "Tabs"
}: LiquidGlassTabBarProps) {
  const baseCanvasRef = useRef<HTMLCanvasElement>(null);
  const indicatorCanvasRef = useRef<HTMLCanvasElement>(null);
  const baseRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const indicatorRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const motionFrameRef = useRef(0);
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
    morph: TAB_STATIC_MORPH,
    targetMorph: TAB_STATIC_MORPH,
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
    blur: .24,
    refraction: .36,
    chromaticAberration: .03,
    distortion: .012,
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
    // 调用方 settings 放最后，确保 tint/brightness/darkTint 等能真正被外部覆盖
    ...settings
  }), [settings, variant]);

  const cellWidth = () => (width - BAR_INSET * 2) / Math.max(1, items.length);
  const itemCenter = (index: number) => BAR_INSET + cellWidth() * (index + .5);

  const syncBase = (stretch: number, pressed: boolean) => {
    baseRendererRef.current?.setGeometry(width / 2, BAR_HEIGHT / 2, stretch, pressed, 1, 1, 0);
  };

  const animateBase = () => {
    const drag = dragRef.current;
    drag.velocity += (drag.target - drag.stretch) * mergedSettings.liquidSpring;
    drag.velocity *= mergedSettings.liquidDamping;
    drag.stretch = Math.max(-.025, Math.min(.08, drag.stretch + drag.velocity));
    drag.target *= drag.active ? .9 : .7;
    syncBase(drag.stretch, drag.active);
    if (Math.abs(drag.target) + Math.abs(drag.stretch) + Math.abs(drag.velocity) > .001) {
      motionFrameRef.current = requestAnimationFrame(animateBase);
    } else {
      drag.target = 0;
      drag.stretch = 0;
      drag.velocity = 0;
      syncBase(0, false);
    }
  };

  const syncIndicator = () => {
    const indicator = indicatorRef.current;
    indicatorRendererRef.current?.setGeometry(indicator.x, BAR_HEIGHT / 2, indicator.stretch, indicator.active, indicator.morph, 1, 0);
  };

  const animateIndicator = () => {
    const indicator = indicatorRef.current;
    if (!indicator.active) {
      const previousError = indicator.targetX - indicator.x;
      indicator.velocityX += previousError * TAB_POSITION_SPRING;
      indicator.velocityX *= TAB_POSITION_DAMPING;
      const nextX = indicator.x + indicator.velocityX;
      if ((indicator.targetX - nextX) * previousError <= 0) {
        indicator.x = indicator.targetX;
        indicator.velocityX = 0;
      } else {
        indicator.x = nextX;
      }
    }
    indicator.stretchVelocity += (indicator.targetStretch - indicator.stretch) * TAB_SHAPE_SPRING;
    indicator.stretchVelocity *= TAB_SHAPE_DAMPING;
    indicator.stretch = Math.max(0, Math.min(.16, indicator.stretch + indicator.stretchVelocity));
    if (indicator.stretch === 0 && indicator.stretchVelocity < 0) indicator.stretchVelocity = 0;
    indicator.targetStretch *= indicator.active ? .9 : .54;
    const morphDelta = indicator.targetMorph - indicator.morph;
    indicator.morph = Math.max(
      TAB_STATIC_MORPH,
      Math.min(1, Math.abs(morphDelta) < .004 ? indicator.targetMorph : indicator.morph + morphDelta * TAB_MORPH_EASE)
    );
    syncIndicator();
    const positionSettled = indicator.active || Math.abs(indicator.targetX - indicator.x) + Math.abs(indicator.velocityX) < .02;
    const shapeSettled = Math.abs(indicator.targetStretch) + Math.abs(indicator.stretch) + Math.abs(indicator.stretchVelocity) < .002;
    const morphSettled = Math.abs(indicator.targetMorph - indicator.morph) < .006;
    const materialAtRest = Math.abs(indicator.targetX - indicator.x) + Math.abs(indicator.velocityX) < .45;
    if (!indicator.active && !indicator.glassReleased && materialAtRest) {
      indicator.targetMorph = TAB_STATIC_MORPH;
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
      syncIndicator();
    }
  };

  const moveIndicatorTo = (index: number, settleToStatic = true) => {
    const indicator = indicatorRef.current;
    indicator.targetX = itemCenter(index);
    indicator.targetMorph = settleToStatic ? TAB_STATIC_MORPH : 1;
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
      const baseSettings = { ...mergedSettings, lensWidth: width - 4, lensHeight: BAR_HEIGHT - 8, radius: 32 };
      const indicatorSettings = {
        ...mergedSettings,
        lensWidth: cellWidth() + 18,
        lensHeight: 62,
        radius: 31,
        brightness: -.03,
        darkTint: .24,
        edgeHighlight: .16,
        specular: .18,
        shadow: 0
      };
      const baseRenderer = new LiquidGlassRenderer(baseCanvas, backgroundImage, baseSettings);
      const indicatorRenderer = new LiquidGlassRenderer(indicatorCanvas, backgroundImage, indicatorSettings);
      baseRenderer.setBackgroundSampling(sampleBackground);
      indicatorRenderer.setBackgroundSampling(sampleBackground);
      baseRenderer.resize(width, BAR_HEIGHT);
      indicatorRenderer.resize(width, BAR_HEIGHT);
      baseRenderer.setSettings(baseSettings);
      indicatorRenderer.setSettings(indicatorSettings);
      baseRenderer.setTrack(-1000, -900, -1000, -950);
      indicatorRenderer.setTrack(-1000, -900, -1000, -950);
      baseRendererRef.current = baseRenderer;
      indicatorRendererRef.current = indicatorRenderer;
      const initialX = itemCenter(selectedIndexRef.current);
      indicatorRef.current.x = initialX;
      indicatorRef.current.targetX = initialX;
      indicatorRef.current.morph = TAB_STATIC_MORPH;
      indicatorRef.current.targetMorph = TAB_STATIC_MORPH;
      syncBase(0, false);
      syncIndicator();
    } catch (error) {
      console.warn(error);
    }
    return () => {
      if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
      cancelAnimationFrame(motionFrameRef.current);
      cancelAnimationFrame(indicatorFrameRef.current);
      baseRendererRef.current?.dispose();
      indicatorRendererRef.current?.dispose();
      baseRendererRef.current = null;
      indicatorRendererRef.current = null;
    };
  }, [backgroundImage, items.length, mergedSettings, width, sampleBackground]);

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
      className={`lg-tabbar ${draggable ? "is-draggable" : ""} ${className}`}
      data-indicator-moving={indicatorStaticHidden ? "true" : "false"}
      style={{
        width,
        ...(draggable ? { transform: `translate3d(${currentPosition.x}px, ${currentPosition.y}px, 0)` } : {}),
      }}
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
        cancelAnimationFrame(motionFrameRef.current);
        motionFrameRef.current = requestAnimationFrame(animateBase);
      }}
      onPointerUp={(event) => {
        const drag = dragRef.current;
        if (!draggable || !drag.active) return;
        drag.active = false;
        drag.target = 0;
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        cancelAnimationFrame(motionFrameRef.current);
        motionFrameRef.current = requestAnimationFrame(animateBase);
      }}
      onPointerCancel={() => {
        dragRef.current.active = false;
        dragRef.current.moved = false;
        dragRef.current.target = 0;
        cancelAnimationFrame(motionFrameRef.current);
        motionFrameRef.current = requestAnimationFrame(animateBase);
      }}
    >
      <canvas ref={baseCanvasRef} className="lg-tabbar__glass" aria-hidden="true" />
      <canvas
        ref={indicatorCanvasRef}
        className={`lg-tabbar__indicator-glass ${indicatorGlassVisible ? "is-visible" : ""}`}
        aria-hidden="true"
      />
      <div
        className="lg-tabbar__items"
        role="tablist"
        aria-label={ariaLabel}
        style={{ gridTemplateColumns: `repeat(${items.length}, 1fr)` }}
      >
        {items.map((item, index) => (
          <button
            type="button"
            role="tab"
            key={item.id}
            aria-selected={selectedValue === item.id}
            className={`lg-tabbar__item ${selectedValue === item.id ? "is-selected" : ""}`}
            onClick={() => {
              if (indicatorRef.current.moved) {
                indicatorRef.current.moved = false;
                return;
              }
              if (indicatorFadeTimeoutRef.current !== null) window.clearTimeout(indicatorFadeTimeoutRef.current);
              setIndicatorGlassVisible(true);
              setIndicatorStaticHidden(true);
              indicatorRef.current.glassReleased = false;
              indicatorRef.current.morph = TAB_STATIC_MORPH;
              indicatorRef.current.targetMorph = 1;
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
              indicator.morph = TAB_STATIC_MORPH;
              indicator.targetMorph = 1;
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
              const rect = baseCanvasRef.current?.getBoundingClientRect();
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
              const nextIndex = Math.max(0, Math.min(items.length - 1, Math.round((indicator.x - BAR_INSET - cellWidth() * .5) / cellWidth())));
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
              indicator.targetMorph = TAB_STATIC_MORPH;
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
              indicator.targetMorph = TAB_STATIC_MORPH;
              cancelAnimationFrame(indicatorFrameRef.current);
              indicatorFrameRef.current = requestAnimationFrame(animateIndicator);
            }}
          >
            {item.icon ? <span className="lg-tabbar__icon">{item.icon}</span> : null}
            <span className="lg-tabbar__label">{item.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
