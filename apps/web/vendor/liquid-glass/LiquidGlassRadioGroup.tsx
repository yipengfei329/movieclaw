import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const RADIO_SIZE = 38;

export interface LiquidGlassRadioItem {
  id: string;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}

export interface LiquidGlassRadioGroupProps {
  value?: string;
  defaultValue?: string;
  disabled?: boolean;
  backgroundImage: string;
  items?: LiquidGlassRadioItem[];
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  onValueChange?: (value: string) => void;
  className?: string;
  "aria-label"?: string;
}

const defaultItems: LiquidGlassRadioItem[] = [
  { id: "clear", label: "Clear", description: "Bright glass with strong highlights" },
  { id: "dark", label: "Dark", description: "Deeper material for overlays" },
  { id: "prism", label: "Prism", description: "More chromatic refraction" }
];

interface RadioOptionProps {
  item: LiquidGlassRadioItem;
  selected: boolean;
  groupDisabled: boolean;
  backgroundImage: string;
  variant: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  onSelect: () => void;
}

function RadioOption({ item, selected, groupDisabled, backgroundImage, variant, settings, onSelect }: RadioOptionProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const motionRef = useRef({ stretch: 0, target: 0, velocity: 0 });
  const [pressed, setPressed] = useState(false);
  const disabled = groupDisabled || !!item.disabled;
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, {
    depth: 34,
    blur: .24,
    refraction: .38,
    chromaticAberration: .03,
    distortion: .012,
    edgeHighlight: .12,
    specular: .16,
    fresnel: 1,
    darkTint: .28,
    tintStrength: .1,
    opacity: 1,
    shadow: 0,
    bevel: 0,
    ...settings,
    lensWidth: 34,
    lensHeight: 34,
    radius: 17
  }), [settings, variant]);

  const syncMotion = (stretch: number, active: boolean) => {
    rendererRef.current?.setGeometry(RADIO_SIZE / 2, RADIO_SIZE / 2, stretch, active, 1, 1, 0);
  };

  const animateMotion = () => {
    const motion = motionRef.current;
    motion.velocity += (motion.target - motion.stretch) * mergedSettings.liquidSpring;
    motion.velocity *= mergedSettings.liquidDamping;
    motion.stretch = Math.max(-.025, Math.min(.09, motion.stretch + motion.velocity));
    motion.target *= pressed ? .86 : .62;
    syncMotion(motion.stretch, pressed);
    if (Math.abs(motion.target) + Math.abs(motion.stretch) + Math.abs(motion.velocity) > .001) {
      frameRef.current = requestAnimationFrame(animateMotion);
    } else {
      motion.target = 0;
      motion.stretch = 0;
      motion.velocity = 0;
      syncMotion(0, false);
    }
  };

  const pulse = (amount = .055) => {
    motionRef.current.target = amount;
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(animateMotion);
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      const renderer = new LiquidGlassRenderer(canvas, backgroundImage, mergedSettings);
      renderer.setBackgroundSampling(true);
      renderer.resize(RADIO_SIZE, RADIO_SIZE);
      renderer.setTrack(-1000, -900, -1000, -950);
      renderer.setGeometry(RADIO_SIZE / 2, RADIO_SIZE / 2, 0, false, 1, 1, 0);
      rendererRef.current = renderer;
    } catch (error) {
      console.warn(error);
    }
    return () => {
      cancelAnimationFrame(frameRef.current);
      rendererRef.current?.dispose();
      rendererRef.current = null;
    };
  }, []);

  useEffect(() => {
    rendererRef.current?.setImage(backgroundImage);
  }, [backgroundImage]);

  useEffect(() => {
    rendererRef.current?.setSettings(mergedSettings);
    syncMotion(pressed ? .025 : motionRef.current.stretch, pressed);
  }, [mergedSettings, pressed]);

  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      className={`lg-radio__item ${selected ? "is-selected" : ""} ${pressed ? "is-pressed" : ""}`}
      onPointerDown={() => {
        if (disabled) return;
        setPressed(true);
        pulse(.03);
      }}
      onPointerUp={() => setPressed(false)}
      onPointerCancel={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      onClick={() => {
        if (disabled) return;
        onSelect();
        pulse(.055);
      }}
    >
      <span className="lg-radio__control">
        <canvas ref={canvasRef} className="lg-radio__glass" aria-hidden="true" />
        <span className="lg-radio__active-surface" aria-hidden="true" />
        <span className="lg-radio__dot" aria-hidden="true" />
      </span>
      <span className="lg-radio__copy">
        <span className="lg-radio__label">{item.label}</span>
        {item.description ? <span className="lg-radio__description">{item.description}</span> : null}
      </span>
    </button>
  );
}

export function LiquidGlassRadioGroup({
  value,
  defaultValue,
  disabled = false,
  backgroundImage,
  items = defaultItems,
  variant = "dark",
  settings,
  onValueChange,
  className = "",
  "aria-label": ariaLabel = "Liquid glass radio group"
}: LiquidGlassRadioGroupProps) {
  const initialValue = defaultValue ?? items[0]?.id ?? "";
  const [internalValue, setInternalValue] = useState(initialValue);
  const currentValue = value ?? internalValue;

  const commit = (next: string) => {
    if (value === undefined) setInternalValue(next);
    onValueChange?.(next);
  };

  return (
    <div className={`lg-radio ${className}`} role="radiogroup" aria-label={ariaLabel}>
      {items.map((item) => (
        <RadioOption
          key={item.id}
          item={item}
          selected={currentValue === item.id}
          groupDisabled={disabled}
          backgroundImage={backgroundImage}
          variant={variant}
          settings={settings}
          onSelect={() => commit(item.id)}
        />
      ))}
    </div>
  );
}
