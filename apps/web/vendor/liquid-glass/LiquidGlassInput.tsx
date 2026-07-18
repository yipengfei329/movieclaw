import {
  forwardRef,
  useEffect,
  useMemo,
  useRef,
  useState,
  type InputHTMLAttributes,
  type PointerEvent as ReactPointerEvent
} from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

export interface LiquidGlassInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "size"> {
  backgroundImage: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  containerClassName?: string;
}

export const LiquidGlassInput = forwardRef<HTMLInputElement, LiquidGlassInputProps>(
  function LiquidGlassInput(
    {
      backgroundImage,
      variant = "frosted",
      settings,
      containerClassName = "",
      className = "",
      placeholder = "Search",
      onFocus,
      onBlur,
      ...inputProps
    },
    forwardedRef
  ) {
    const rootRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const rendererRef = useRef<LiquidGlassRenderer | null>(null);
    const [focused, setFocused] = useState(false);
    const [pressed, setPressed] = useState(false);
    const mergedSettings = useMemo(
      () => ({
        ...resolveLiquidGlassSettings(variant, settings),
        lensWidth: 270,
        lensHeight: 44,
        radius: 22,
        depth: Math.min(22, resolveLiquidGlassSettings(variant, settings).depth),
        shadow: 0
      }),
      [variant, settings]
    );

    const syncRenderer = () => {
      const root = rootRef.current;
      if (!root) return;
      const fieldLeft = 0;
      const fieldWidth = root.clientWidth - fieldLeft;
      rendererRef.current?.setTrack(-1000, -900, -1000, -950);
      rendererRef.current?.setGeometry(
        fieldLeft + fieldWidth / 2,
        root.clientHeight / 2,
        focused ? 0.025 : 0,
        pressed
      );
    };

    useEffect(() => {
      if (!canvasRef.current || !rootRef.current) return;
      try {
        rendererRef.current = new LiquidGlassRenderer(
          canvasRef.current,
          backgroundImage,
          mergedSettings
        );
        rendererRef.current.setBackgroundSampling(true);
      } catch (error) {
        console.warn(error);
      }
      const observer = new ResizeObserver(([entry]) => {
        rendererRef.current?.resize(entry.contentRect.width, entry.contentRect.height);
        syncRenderer();
      });
      observer.observe(rootRef.current);
      return () => observer.disconnect();
    }, []);

    useEffect(() => {
      rendererRef.current?.setImage(backgroundImage);
    }, [backgroundImage]);

    useEffect(() => {
      rendererRef.current?.setSettings(mergedSettings);
      syncRenderer();
    }, [mergedSettings, focused, pressed]);

    const releasePress = () => setPressed(false);
    const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
      if ((event.target as HTMLElement).closest(".lg-input__input")) return;
      setPressed(true);
    };

    return (
      <div
        ref={rootRef}
        className={`lg-input ${focused ? "is-focused" : ""} ${pressed ? "is-pressed" : ""} ${containerClassName}`}
        onPointerDown={handlePointerDown}
        onPointerUp={releasePress}
        onPointerCancel={releasePress}
        onPointerLeave={releasePress}
      >
        <canvas ref={canvasRef} className="lg-input__glass" aria-hidden="true" />
        <label className="lg-input__field">
          <svg className="lg-input__icon" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="10.75" cy="10.75" r="6.25" />
            <path d="m15.5 15.5 4 4" />
          </svg>
          <input
            {...inputProps}
            ref={forwardedRef}
            className={`lg-input__input ${className}`}
            type="search"
            placeholder={placeholder}
            onFocus={(event) => {
              setFocused(true);
              onFocus?.(event);
            }}
            onBlur={(event) => {
              setFocused(false);
              setPressed(false);
              onBlur?.(event);
            }}
          />
        </label>
      </div>
    );
  }
);

export type LiquidGlassSearchProps = LiquidGlassInputProps;
export const LiquidGlassSearch = LiquidGlassInput;
