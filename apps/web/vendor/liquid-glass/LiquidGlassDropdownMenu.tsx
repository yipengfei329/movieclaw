import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const TRIGGER_WIDTH = 300;
const TRIGGER_HEIGHT = 64;
const MENU_PAD_X = 18;
const MENU_PAD_Y = 12;

export interface LiquidGlassDropdownItem {
  id: string;
  label: string;
  description?: string;
  icon?: React.ReactNode;
  disabled?: boolean;
}

export interface LiquidGlassDropdownMenuProps {
  backgroundImage: string;
  items?: LiquidGlassDropdownItem[];
  value?: string;
  defaultValue?: string;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  onValueChange?: (value: string) => void;
  label?: React.ReactNode;
  placeholder?: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  draggable?: boolean;
  position?: { x: number; y: number };
  defaultPosition?: { x: number; y: number };
  onPositionChange?: (position: { x: number; y: number }) => void;
  className?: string;
  "aria-label"?: string;
}

const defaultItems: LiquidGlassDropdownItem[] = [
  { id: "overview", label: "Overview", description: "Project summary" },
  { id: "analytics", label: "Analytics", description: "Performance data" },
  { id: "exports", label: "Exports", description: "Download assets" },
  { id: "settings", label: "Settings", description: "Workspace options" }
];

function Chevron() {
  return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 8 5 5 5-5" /></svg>;
}

function Check() {
  return <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg>;
}

export function LiquidGlassDropdownMenu({
  backgroundImage,
  items = defaultItems,
  value,
  defaultValue,
  open,
  defaultOpen = false,
  onOpenChange,
  onValueChange,
  label = "View",
  placeholder = "Select",
  variant = "dark",
  settings,
  draggable = false,
  position,
  defaultPosition = { x: 420, y: 280 },
  onPositionChange,
  className = "",
  "aria-label": ariaLabel = "Dropdown menu"
}: LiquidGlassDropdownMenuProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerCanvasRef = useRef<HTMLCanvasElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuCanvasRef = useRef<HTMLCanvasElement>(null);
  const triggerRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const menuRendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const closeTimerRef = useRef<number | null>(null);
  const menuSizeRef = useRef({ width: 0, height: 0 });
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
  const initialValue = defaultValue ?? items[0]?.id ?? "";
  const [internalValue, setInternalValue] = useState(initialValue);
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const [mounted, setMounted] = useState(open ?? defaultOpen);
  const [internalPosition, setInternalPosition] = useState(defaultPosition);
  const currentValue = value ?? internalValue;
  const currentOpen = open ?? internalOpen;
  const currentPosition = position ?? internalPosition;
  const selectedItem = items.find((item) => item.id === currentValue);
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, {
    blur: .18,
    refraction: .72,
    chromaticAberration: .045,
    distortion: .015,
    edgeHighlight: .08,
    specular: .14,
    fresnel: 1.08,
    radius: 22,
    depth: 42,
    brightness: -.03,
    saturation: .03,
    darkTint: .28,
    tintStrength: .06,
    tint: -.76,
    opacity: 1,
    lensWidth: 54,
    lensHeight: 34,
    liquidMotion: .14,
    liquidSpring: .055,
    liquidDamping: .84,
    shadow: 0,
    bevel: 0,
    ...settings
  }), [settings, variant]);

  const commitOpen = (next: boolean) => {
    if (open === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };

  const commitValue = (next: string) => {
    if (value === undefined) setInternalValue(next);
    onValueChange?.(next);
  };

  const syncMotion = (stretch: number, pressed: boolean) => {
    triggerRendererRef.current?.setGeometry(TRIGGER_WIDTH / 2, TRIGGER_HEIGHT / 2, stretch * .45, pressed, 1, 1, 0);
    const { width, height } = menuSizeRef.current;
    if (width && height) {
      menuRendererRef.current?.setGeometry(width / 2 + MENU_PAD_X, height / 2 + MENU_PAD_Y, stretch, pressed, 1, 1, 0);
    }
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
      return;
    }
    closeTimerRef.current = window.setTimeout(() => {
      setMounted(false);
      closeTimerRef.current = null;
    }, 340);
    return () => {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    };
  }, [currentOpen]);

  useEffect(() => {
    const canvas = triggerCanvasRef.current;
    if (!canvas) return;
    try {
      const renderer = new LiquidGlassRenderer(canvas, backgroundImage, {
        ...mergedSettings,
        lensWidth: TRIGGER_WIDTH,
        lensHeight: TRIGGER_HEIGHT,
        radius: TRIGGER_HEIGHT / 2
      });
      renderer.setBackgroundSampling(true);
      renderer.resize(TRIGGER_WIDTH, TRIGGER_HEIGHT);
      renderer.setTrack(-1000, -900, -1000, -950);
      renderer.setGeometry(TRIGGER_WIDTH / 2, TRIGGER_HEIGHT / 2, 0, false, 1, 1, 0);
      triggerRendererRef.current = renderer;
    } catch (error) {
      console.warn(error);
    }
    return () => {
      triggerRendererRef.current?.dispose();
      triggerRendererRef.current = null;
    };
  }, [backgroundImage, mergedSettings]);

  useEffect(() => {
    if (!mounted) return;
    const menu = menuRef.current;
    const canvas = menuCanvasRef.current;
    if (!menu || !canvas) return;
    let renderer: LiquidGlassRenderer;
    try {
      renderer = new LiquidGlassRenderer(canvas, backgroundImage, mergedSettings);
      renderer.setBackgroundSampling(true);
      renderer.setTrack(-1000, -900, -1000, -950);
      menuRendererRef.current = renderer;
    } catch (error) {
      console.warn(error);
      return;
    }
    const resize = (width: number, height: number) => {
      if (!width || !height) return;
      menuSizeRef.current = { width, height };
      renderer.resize(width + MENU_PAD_X * 2, height + MENU_PAD_Y * 2);
      renderer.setSettings({
        ...mergedSettings,
        lensWidth: Math.max(1, width - 4),
        lensHeight: Math.max(1, height - 4),
        radius: Math.min(mergedSettings.radius, height / 2)
      });
      syncMotion(dragRef.current.stretch, dragRef.current.active);
    };
    const observer = new ResizeObserver(([entry]) => resize(entry.contentRect.width, entry.contentRect.height));
    observer.observe(menu);
    resize(menu.clientWidth, menu.clientHeight);
    return () => {
      observer.disconnect();
      menuRendererRef.current?.dispose();
      menuRendererRef.current = null;
    };
  }, [backgroundImage, mergedSettings, mounted]);

  useEffect(() => {
    if (!currentOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) commitOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [currentOpen]);

  const setDropdownPosition = (next: { x: number; y: number }) => {
    if (position === undefined) setInternalPosition(next);
    onPositionChange?.(next);
  };

  return (
    <div
      ref={rootRef}
      className={`lg-dropdown ${draggable ? "is-draggable" : ""} ${className}`}
      style={draggable ? { transform: `translate3d(${currentPosition.x}px, ${currentPosition.y}px, 0)` } : undefined}
      onClickCapture={(event) => {
        if (!dragRef.current.moved) return;
        event.preventDefault();
        event.stopPropagation();
        dragRef.current.moved = false;
      }}
      onPointerDown={(event) => {
        if (!draggable) return;
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
        setDropdownPosition({ x: event.clientX - drag.offsetX, y: event.clientY - drag.offsetY });
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
      <button
        type="button"
        className="lg-dropdown__trigger"
        aria-haspopup="menu"
        aria-expanded={currentOpen}
        aria-label={ariaLabel}
        onClick={() => commitOpen(!currentOpen)}
      >
        <canvas ref={triggerCanvasRef} className="lg-dropdown__trigger-glass" aria-hidden="true" />
        <span className="lg-dropdown__trigger-content">
          <span className="lg-dropdown__trigger-copy">
            <span className="lg-dropdown__trigger-label">{label}</span>
            <strong>{selectedItem?.label ?? placeholder}</strong>
          </span>
          <span className="lg-dropdown__chevron"><Chevron /></span>
        </span>
      </button>
      {mounted ? (
        <div
          ref={menuRef}
          className={`lg-dropdown__menu ${currentOpen ? "is-open" : ""}`}
          role="menu"
        >
          <canvas ref={menuCanvasRef} className="lg-dropdown__menu-glass" aria-hidden="true" />
          <div className="lg-dropdown__items">
            {items.map((item) => (
              <button
                type="button"
                role="menuitemradio"
                aria-checked={currentValue === item.id}
                disabled={item.disabled}
                className={`lg-dropdown__item ${currentValue === item.id ? "is-selected" : ""}`}
                key={item.id}
                onClick={() => {
                  if (item.disabled) return;
                  commitValue(item.id);
                  commitOpen(false);
                }}
              >
                {item.icon ? <span className="lg-dropdown__item-icon">{item.icon}</span> : null}
                <span className="lg-dropdown__item-copy">
                  <span>{item.label}</span>
                  {item.description ? <small>{item.description}</small> : null}
                </span>
                <span className="lg-dropdown__check"><Check /></span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
