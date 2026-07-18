import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const BOX_SIZE = 38;

export interface LiquidGlassCheckboxProps {
  checked?: boolean;
  defaultChecked?: boolean;
  disabled?: boolean;
  backgroundImage: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  onCheckedChange?: (checked: boolean) => void;
  label?: React.ReactNode;
  description?: React.ReactNode;
  className?: string;
  "aria-label"?: string;
}

export function LiquidGlassCheckbox({
  checked,
  defaultChecked = false,
  disabled = false,
  backgroundImage,
  variant = "dark",
  settings,
  onCheckedChange,
  label,
  description,
  className = "",
  "aria-label": ariaLabel = "Liquid glass checkbox"
}: LiquidGlassCheckboxProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<LiquidGlassRenderer | null>(null);
  const frameRef = useRef(0);
  const motionRef = useRef({ stretch: 0, target: 0, velocity: 0 });
  const [internalChecked, setInternalChecked] = useState(defaultChecked);
  const [pressed, setPressed] = useState(false);
  const currentChecked = checked ?? internalChecked;
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
    radius: 11
  }), [settings, variant]);

  const syncMotion = (stretch: number, active: boolean) => {
    rendererRef.current?.setGeometry(BOX_SIZE / 2, BOX_SIZE / 2, stretch, active, 1, 1, 0);
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

  const commit = () => {
    if (disabled) return;
    const next = !currentChecked;
    if (checked === undefined) setInternalChecked(next);
    onCheckedChange?.(next);
    motionRef.current.target = .055;
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(animateMotion);
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      const renderer = new LiquidGlassRenderer(canvas, backgroundImage, mergedSettings);
      renderer.setBackgroundSampling(true);
      renderer.resize(BOX_SIZE, BOX_SIZE);
      renderer.setTrack(-1000, -900, -1000, -950);
      renderer.setGeometry(BOX_SIZE / 2, BOX_SIZE / 2, 0, false, 1, 1, 0);
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
      role="checkbox"
      aria-checked={currentChecked}
      aria-label={label ? undefined : ariaLabel}
      disabled={disabled}
      className={`lg-checkbox ${currentChecked ? "is-checked" : ""} ${pressed ? "is-pressed" : ""} ${className}`}
      onPointerDown={() => {
        if (disabled) return;
        setPressed(true);
        motionRef.current.target = .03;
        cancelAnimationFrame(frameRef.current);
        frameRef.current = requestAnimationFrame(animateMotion);
      }}
      onPointerUp={() => setPressed(false)}
      onPointerCancel={() => setPressed(false)}
      onPointerLeave={() => setPressed(false)}
      onClick={commit}
    >
      <span className="lg-checkbox__box">
        <canvas ref={canvasRef} className="lg-checkbox__glass" aria-hidden="true" />
        <span className="lg-checkbox__active-surface" aria-hidden="true" />
        <svg className="lg-checkbox__mark" viewBox="0 0 24 24" aria-hidden="true">
          <path d="m5 12 4 4 10-10" />
        </svg>
      </span>
      {label || description ? (
        <span className="lg-checkbox__copy">
          {label ? <span className="lg-checkbox__label">{label}</span> : null}
          {description ? <span className="lg-checkbox__description">{description}</span> : null}
        </span>
      ) : null}
    </button>
  );
}
