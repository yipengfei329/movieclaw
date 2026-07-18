import { useEffect, useMemo, useRef, useState } from "react";
import { LiquidGlassRenderer } from "./core/LiquidGlassRenderer";
import {
  resolveLiquidGlassSettings,
  type LiquidGlassSettings,
  type LiquidGlassVariant
} from "./core/types";

const PALETTE_PAD_X = 24;
const PALETTE_PAD_Y = 14;

export interface LiquidGlassCommandItem {
  id: string;
  label: string;
  description?: string;
  shortcut?: string;
  icon?: React.ReactNode;
  disabled?: boolean;
}

export interface LiquidGlassCommandPaletteProps {
  backgroundImage: string;
  items?: LiquidGlassCommandItem[];
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  onSelect?: (id: string) => void;
  /**
   * 「提交」回调。传入后面板进入「搜索提交」语义：回车不再是选中列表项，而是把当前输入
   * 原样交给上层去发起一次搜索（用于关键词搜索类场景）。返回值由上层决定是否关闭面板。
   */
  onSubmit?: (query: string) => void;
  /**
   * 自定义主体区。传入后**替换**默认的候选项列表（例如渲染分类标签 + 搜索提示）。
   * 此时面板不做 items 列表渲染与上下键导航，交互由 bodySlot 与 onSubmit 决定。
   */
  bodySlot?: React.ReactNode;
  /**
   * 自定义头部区，渲染在搜索输入框**上方**（例如把分类分段放到输入框之上）。
   */
  headerSlot?: React.ReactNode;
  placeholder?: string;
  emptyText?: string;
  variant?: LiquidGlassVariant;
  settings?: Partial<LiquidGlassSettings>;
  /**
   * 是否让玻璃采样并折射背景图。默认 true（透出并折射背景，液态玻璃观感）。
   * 传 false 时改用恒定近黑底，得到「均匀深色、不透亮背景」的实心面板观感
   * （与侧栏 sampleBackground={false} 一致）。
   */
  sampleBackground?: boolean;
  className?: string;
  "aria-label"?: string;
}

const defaultItems: LiquidGlassCommandItem[] = [
  { id: "new-file", label: "New file", description: "Create a blank component file", shortcut: "N" },
  { id: "search", label: "Search components", description: "Find library surfaces", shortcut: "/" },
  { id: "theme", label: "Switch glass variant", description: "Cycle visual presets", shortcut: "T" },
  { id: "export", label: "Export package", description: "Build the React package", shortcut: "E" }
];

export function LiquidGlassCommandPalette({
  backgroundImage,
  items = defaultItems,
  open,
  defaultOpen = false,
  onOpenChange,
  onSelect,
  onSubmit,
  bodySlot,
  headerSlot,
  placeholder = "Search commands",
  emptyText = "No commands found",
  variant = "dark",
  settings,
  sampleBackground = true,
  className = "",
  "aria-label": ariaLabel = "Command palette"
}: LiquidGlassCommandPaletteProps) {
  const rootRef = useRef<HTMLElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const rendererRef = useRef<LiquidGlassRenderer | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const [mounted, setMounted] = useState(open ?? defaultOpen);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const currentOpen = open ?? internalOpen;
  const mergedSettings = useMemo(() => resolveLiquidGlassSettings(variant, {
    blur: .34,
    refraction: .4,
    chromaticAberration: .03,
    distortion: .014,
    edgeHighlight: .12,
    specular: .16,
    fresnel: 1,
    depth: 40,
    brightness: -.08,
    saturation: -.08,
    darkTint: .44,
    tintStrength: .14,
    opacity: 1,
    shadow: 0,
    bevel: 0,
    ...settings
  }), [settings, variant]);

  const filteredItems = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((item) => {
      const haystack = `${item.label} ${item.description ?? ""} ${item.shortcut ?? ""}`.toLowerCase();
      return haystack.includes(needle);
    });
  }, [items, query]);

  const commitOpen = (next: boolean) => {
    if (open === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };

  const syncGeometry = () => {
    const { width, height } = sizeRef.current;
    if (!width || !height) return;
    rendererRef.current?.setGeometry(width / 2 + PALETTE_PAD_X, height / 2 + PALETTE_PAD_Y, currentOpen ? .015 : 0, currentOpen, 1, 1, 0);
  };

  useEffect(() => {
    if (currentOpen) {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
      setMounted(true);
      setActiveIndex(0);
      return;
    }
    closeTimerRef.current = window.setTimeout(() => {
      setMounted(false);
      closeTimerRef.current = null;
      setQuery("");
    }, 360);
    return () => {
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    };
  }, [currentOpen]);

  // 自动聚焦必须等 mounted=true 的那次提交之后再做：关闭状态下组件渲染 null、
  // 输入框不在 DOM 里，若在 currentOpen 副作用里 rAF 聚焦，回调常跑在重新挂载
  // 提交之前 inputRef 还是 null（表现为重开面板后输入框不聚焦）。effect 执行时
  // DOM 已提交，直接同步 focus 即可——不用 rAF，后台标签页里 rAF 会被挂起。
  useEffect(() => {
    if (mounted && currentOpen) inputRef.current?.focus();
  }, [mounted, currentOpen]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    if (!mounted) return;
    const root = rootRef.current;
    const canvas = canvasRef.current;
    if (!root || !canvas) return;
    let renderer: LiquidGlassRenderer;
    try {
      renderer = new LiquidGlassRenderer(canvas, backgroundImage, mergedSettings);
      // 关闭采样时 shader 用恒定近黑底（不折射偏亮的背景图），得到「均匀深色」的实心观感
      renderer.setBackgroundSampling(sampleBackground);
      renderer.setTrack(-1000, -900, -1000, -950);
      rendererRef.current = renderer;
    } catch (error) {
      console.warn(error);
      return;
    }
    const resize = (width: number, height: number) => {
      if (!width || !height) return;
      sizeRef.current = { width, height };
      renderer.resize(width + PALETTE_PAD_X * 2, height + PALETTE_PAD_Y * 2);
      renderer.setSettings({
        ...mergedSettings,
        lensWidth: Math.max(1, width - 4),
        lensHeight: Math.max(1, height - 4),
        radius: Math.min(mergedSettings.radius, height / 2)
      });
      syncGeometry();
    };
    const observer = new ResizeObserver(([entry]) => resize(entry.contentRect.width, entry.contentRect.height));
    observer.observe(root);
    resize(root.clientWidth, root.clientHeight);
    return () => {
      observer.disconnect();
      renderer.dispose();
      rendererRef.current = null;
    };
  }, [backgroundImage, mergedSettings, mounted, sampleBackground]);

  const choose = (item: LiquidGlassCommandItem) => {
    if (item.disabled) return;
    onSelect?.(item.id);
    commitOpen(false);
  };

  if (!mounted) return null;

  return (
    <div className={`lg-command-overlay ${currentOpen ? "is-open" : ""}`}>
      <button type="button" className="lg-command-overlay__backdrop" aria-label="Close command palette" onClick={() => commitOpen(false)} />
      <section
        ref={rootRef}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        className={`lg-command ${className}`}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            commitOpen(false);
          }
          // 搜索提交模式（bodySlot 自定义主体）不做列表导航，回车即提交当前输入
          if (!bodySlot && event.key === "ArrowDown") {
            event.preventDefault();
            setActiveIndex((index) => Math.min(filteredItems.length - 1, index + 1));
          }
          if (!bodySlot && event.key === "ArrowUp") {
            event.preventDefault();
            setActiveIndex((index) => Math.max(0, index - 1));
          }
          if (event.key === "Enter") {
            event.preventDefault();
            if (onSubmit) {
              const q = query.trim();
              if (q) onSubmit(q);
              return;
            }
            const item = filteredItems[activeIndex];
            if (item) choose(item);
          }
        }}
      >
        <canvas ref={canvasRef} className="lg-command__glass" aria-hidden="true" />
        <div className="lg-command__content">
          {headerSlot ? (
            <div className="lg-command__header" style={{ marginBottom: 12 }}>{headerSlot}</div>
          ) : null}
          <div className="lg-command__search">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={placeholder}
              aria-label={placeholder}
            />
          </div>
          {bodySlot ? (
            <div className="lg-command__body">{bodySlot}</div>
          ) : (
          <div className="lg-command__list" role="listbox" aria-label="Commands">
            {filteredItems.length ? filteredItems.map((item, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                disabled={item.disabled}
                className={`lg-command__item ${index === activeIndex ? "is-active" : ""}`}
                key={item.id}
                onPointerMove={() => setActiveIndex(index)}
                onClick={() => choose(item)}
              >
                {item.icon ? <span className="lg-command__icon">{item.icon}</span> : null}
                <span className="lg-command__copy">
                  <span>{item.label}</span>
                  {item.description ? <small>{item.description}</small> : null}
                </span>
                {item.shortcut ? <kbd>{item.shortcut}</kbd> : null}
              </button>
            )) : <div className="lg-command__empty">{emptyText}</div>}
          </div>
          )}
        </div>
      </section>
    </div>
  );
}
