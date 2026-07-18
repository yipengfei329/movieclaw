import { fragmentShader, vertexShader } from "./shaders";
import type { LiquidGlassSettings } from "./types";

type Uniforms = Record<string, WebGLUniformLocation | null>;

const imageCache = new Map<string, HTMLImageElement>();
const isVideoUrl = (url: string) => /\.(mp4|webm|mov)(?:$|[?#])/i.test(url);

export class LiquidGlassRenderer {
  private gl: WebGLRenderingContext;
  private uniforms: Uniforms = {};
  private texture: WebGLTexture;
  private image: HTMLImageElement;
  private video: HTMLVideoElement | null = null;
  private ready = false;
  private settings: LiquidGlassSettings;
  private center = { x: 0, y: 0 };
  private size = { width: 54, height: 34 };
  private stretch = 0;
  private morph = 1;
private materialMorph = 1;
private pressed = false;
private button = 0;
/* 背景采样强度 0~1：0 = 纯中性玻璃底，1 = 完全采样背景图；中间值按比例混合（透明度可调） */
private sampleBackground = 0;
  private track = { start: 0, end: 0, y: 0, value: 0, radius: 2.5 };
  private trackColors = {
    base: [0.31, 0.32, 0.34] as [number, number, number],
    fill: [0.035, 0.50, 1.0] as [number, number, number]
  };
  private disposed = false;
  private positionFrame = 0;
  private lastViewport = { width: 0, height: 0 };
  private lastScroll = { x: Number.NaN, y: Number.NaN };
  private lastRect = { left: Number.NaN, top: Number.NaN, width: Number.NaN, height: Number.NaN };
  private handleImageLoad = () => {
    if (this.disposed) return;
    const { gl } = this;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.image);
    this.ready = true;
    this.draw();
  };
  private handleVideoReady = () => {
    if (this.disposed || !this.video) return;
    this.ready = true;
    void this.video.play().catch(() => {});
    this.draw();
  };

  constructor(private canvas: HTMLCanvasElement, imageUrl: string, settings: LiquidGlassSettings) {
    const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false, antialias: false });
    if (!gl) throw new Error("Liquid Glass requires WebGL.");
    this.gl = gl;
    this.settings = settings;
    this.size = { width: settings.lensWidth, height: settings.lensHeight };
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) throw new Error("Unable to create WebGL shader.");
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const stage = type === gl.VERTEX_SHADER ? "vertex" : "fragment";
        const details = gl.getShaderInfoLog(shader)?.trim();
        throw new Error(
          details || `Liquid Glass ${stage} shader compilation failed (context lost: ${gl.isContextLost()}).`
        );
      }
      return shader;
    };
    const program = gl.createProgram();
    if (!program) throw new Error("Unable to create WebGL program.");
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexShader));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentShader));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "Shader link failed.");
    gl.useProgram(program);
    const quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,1,1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "a_pos");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    [
      "u_bg","u_res","u_center","u_size","u_bgScale","u_bgOffset","u_blurAmount","u_radius",
      "u_zRadius","u_refract","u_chroma","u_edgeHL","u_specular","u_fresnel","u_brightness",
      "u_saturation","u_shadowAlpha","u_shadowSpread","u_darkTint","u_bevelMode","u_button","u_pressed"
      ,"u_trackStart","u_trackEnd","u_trackY","u_valueX","u_distortion","u_tintStrength","u_opacity",
      "u_sampleBackground","u_materialMorph","u_tint"
      ,"u_tintColor","u_trackBaseColor","u_trackFillColor","u_trackRadius"
    ].forEach((name) => { this.uniforms[name] = gl.getUniformLocation(program, name); });
    const texture = gl.createTexture();
    if (!texture) throw new Error("Unable to create WebGL texture.");
    this.texture = texture;
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.uniform1i(this.uniforms.u_bg, 0);
    this.image = new Image();
    this.loadSource(imageUrl);
    this.watchPosition();
  }

  private loadSource(url: string) {
    this.image.removeEventListener("load", this.handleImageLoad);
    this.video?.removeEventListener("loadeddata", this.handleVideoReady);
    this.video = null;
    this.ready = false;
    if (isVideoUrl(url)) {
      const pageVideo = this.canvas.ownerDocument.querySelector<HTMLVideoElement>(
        `[data-liquid-glass-video][src="${CSS.escape(url)}"]`
      );
      const video = pageVideo ?? this.canvas.ownerDocument.createElement("video");
      if (!pageVideo) {
        video.src = url;
        video.muted = true;
        video.loop = true;
        video.autoplay = true;
        video.playsInline = true;
        video.preload = "auto";
      }
      this.video = video;
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth > 0) {
        this.handleVideoReady();
      } else {
        video.addEventListener("loadeddata", this.handleVideoReady, { once: true });
        video.load();
      }
      return;
    }
    const cachedImage = imageCache.get(url);
    this.image = cachedImage ?? new Image();
    if (!cachedImage) {
      this.image.crossOrigin = "anonymous";
      imageCache.set(url, this.image);
      this.image.src = url;
    }
    if (this.image.complete && this.image.naturalWidth > 0) this.handleImageLoad();
    else this.image.addEventListener("load", this.handleImageLoad, { once: true });
  }

  private watchPosition = () => {
    if (this.disposed) return;
    const rect = this.canvas.getBoundingClientRect();
    const viewport = {
      width: window.innerWidth,
      height: window.innerHeight
    };
    const scroll = { x: window.scrollX, y: window.scrollY };
    const visible =
      rect.right > 0 &&
      rect.bottom > 0 &&
      rect.left < viewport.width &&
      rect.top < viewport.height;
    const moved =
      Math.abs(rect.left - this.lastRect.left) > .05 ||
      Math.abs(rect.top - this.lastRect.top) > .05 ||
      Math.abs(rect.width - this.lastRect.width) > .05 ||
      Math.abs(rect.height - this.lastRect.height) > .05 ||
      Math.abs(scroll.x - this.lastScroll.x) > .05 ||
      Math.abs(scroll.y - this.lastScroll.y) > .05 ||
      viewport.width !== this.lastViewport.width ||
      viewport.height !== this.lastViewport.height;

    if (moved || ((this.sampleBackground || this.video) && visible)) {
      this.lastRect = {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height
      };
      this.lastViewport = viewport;
      this.lastScroll = scroll;
      this.draw();
    }
    this.positionFrame = window.requestAnimationFrame(this.watchPosition);
  };

  setImage(url: string) {
    if (this.video?.currentSrc === url || this.video?.src === url || this.image.currentSrc === url || this.image.src === url) return;
    this.loadSource(url);
  }
  setSettings(settings: LiquidGlassSettings) { this.settings = settings; this.size = { width: settings.lensWidth, height: settings.lensHeight }; this.draw(); }
  setGeometry(
    x: number,
    y: number,
    stretch: number,
    pressed: boolean,
    morph = 1,
    materialMorph = 1,
    button = 0
  ) {
    this.center = { x, y };
    this.stretch = stretch;
    this.pressed = pressed;
    this.button = button;
    this.morph = Math.max(0, Math.min(1, morph));
    this.materialMorph = Math.max(0, Math.min(1, materialMorph));
    this.draw();
  }
  setTrack(start: number, end: number, y: number, value: number, radius = 2.5) {
    this.track = { start, end, y, value, radius };
    this.draw();
  }
  setTrackColors(base: [number, number, number], fill: [number, number, number]) {
    this.trackColors = { base, fill };
    this.draw();
  }
  setBackgroundSampling(amount: boolean | number) {
    // 兼容旧的布尔开关；数值则按 0~1 夹取，作为 shader 里中性玻璃与背景的混合系数
    this.sampleBackground =
      typeof amount === "number" ? Math.max(0, Math.min(1, amount)) : amount ? 1 : 0;
    this.draw();
  }
  dispose() {
    this.disposed = true;
    window.cancelAnimationFrame(this.positionFrame);
    this.ready = false;
    this.image.removeEventListener("load", this.handleImageLoad);
    this.video?.removeEventListener("loadeddata", this.handleVideoReady);
    this.gl.deleteTexture(this.texture);
    this.gl.clearColor(0, 0, 0, 0);
    this.gl.clear(this.gl.COLOR_BUFFER_BIT);
  }
  resize(width: number, height: number) {
    const deviceScale = window.devicePixelRatio || 1;
    const compactSupersampling = height <= 80 ? 3 : height <= 140 ? 2.5 : 2;
    const renderScale = Math.min(4, Math.max(deviceScale, compactSupersampling));
    this.canvas.width = Math.max(1, Math.round(width * renderScale));
    this.canvas.height = Math.max(1, Math.round(height * renderScale));
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this.draw();
  }
  draw() {
    if (!this.ready) return;
    const { gl, canvas, settings: s } = this;
    const source = this.video ?? this.image;
    if (this.video) {
      if (this.video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
      gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.video);
    }
    const dpr = canvas.width / Math.max(1, canvas.clientWidth);
    const rect = canvas.getBoundingClientRect();
    const backgroundElement = canvas.ownerDocument.querySelector<HTMLElement>("[data-liquid-glass-background]");
    const measuredBackgroundRect = backgroundElement?.getBoundingClientRect();
    const backgroundRect = measuredBackgroundRect && measuredBackgroundRect.width > 0 && measuredBackgroundRect.height > 0
      ? measuredBackgroundRect
      : {
          left: 0,
          top: 0,
          right: window.innerWidth,
          bottom: window.innerHeight,
          width: window.innerWidth,
          height: window.innerHeight
        };
    const viewportWidth = Math.max(1, backgroundRect.width);
    const viewportHeight = Math.max(1, backgroundRect.height);
    const sourceWidth = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
    const sourceHeight = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
    const imageRatio = sourceWidth / sourceHeight;
    const viewRatio = viewportWidth / viewportHeight;
    const bg = imageRatio > viewRatio
      ? { sx: viewRatio / imageRatio, sy: 1, ox: (1 - viewRatio / imageRatio) / 2, oy: 0 }
      : { sx: 1, sy: imageRatio / viewRatio, ox: 0, oy: (1 - imageRatio / viewRatio) / 2 };
    const localScaleX = rect.width / viewportWidth;
    const localScaleY = rect.height / viewportHeight;
    const localOffsetX = (rect.left - backgroundRect.left) / viewportWidth;
    const localOffsetY = (backgroundRect.bottom - rect.bottom) / viewportHeight;
    const restingWidth = this.button > .5 ? 36 : 34;
    const restingHeight = this.button > .5 ? 24 : 22;
    const baseWidth = restingWidth + (this.size.width - restingWidth) * this.morph;
    const baseHeight = restingHeight + (this.size.height - restingHeight) * this.morph;
    const width = baseWidth * (1 + this.stretch);
    const height = baseHeight * (1 - this.stretch * .48);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0,0,0,0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(this.uniforms.u_res, canvas.width, canvas.height);
    gl.uniform2f(this.uniforms.u_center, this.center.x*dpr, this.center.y*dpr);
    gl.uniform2f(this.uniforms.u_size, width*dpr, height*dpr);
    gl.uniform2f(this.uniforms.u_bgScale, localScaleX * bg.sx, localScaleY * bg.sy);
    gl.uniform2f(
      this.uniforms.u_bgOffset,
      localOffsetX * bg.sx + bg.ox,
      localOffsetY * bg.sy + bg.oy
    );
    gl.uniform1f(this.uniforms.u_blurAmount, s.blur);
    gl.uniform1f(this.uniforms.u_radius, Math.min(s.radius,height*.5)*dpr);
    gl.uniform1f(this.uniforms.u_zRadius, s.depth*dpr);
    gl.uniform1f(this.uniforms.u_refract, s.refraction);
    gl.uniform1f(this.uniforms.u_chroma, s.chromaticAberration);
    gl.uniform1f(this.uniforms.u_edgeHL, s.edgeHighlight);
    gl.uniform1f(this.uniforms.u_specular, s.specular);
    gl.uniform1f(this.uniforms.u_fresnel, s.fresnel);
    gl.uniform1f(this.uniforms.u_brightness, s.brightness);
    gl.uniform1f(this.uniforms.u_saturation, s.saturation);
    gl.uniform1f(this.uniforms.u_shadowAlpha, s.shadow);
    gl.uniform1f(this.uniforms.u_shadowSpread, (12+s.shadow*18)*dpr);
    gl.uniform1f(this.uniforms.u_darkTint, s.darkTint);
    gl.uniform1f(this.uniforms.u_distortion, s.distortion);
    gl.uniform1f(this.uniforms.u_tintStrength, s.tintStrength);
    gl.uniform1f(this.uniforms.u_tint, s.tint);
    gl.uniform3fv(this.uniforms.u_tintColor, s.tintColor);
    gl.uniform1f(this.uniforms.u_opacity, s.opacity);
    gl.uniform1f(this.uniforms.u_sampleBackground, this.sampleBackground);
    gl.uniform1f(this.uniforms.u_materialMorph, this.materialMorph);
    gl.uniform3f(this.uniforms.u_trackBaseColor, this.trackColors.base[0], this.trackColors.base[1], this.trackColors.base[2]);
    gl.uniform3f(this.uniforms.u_trackFillColor, this.trackColors.fill[0], this.trackColors.fill[1], this.trackColors.fill[2]);
    gl.uniform1f(this.uniforms.u_bevelMode, s.bevel);
    gl.uniform1f(this.uniforms.u_button, this.button);
    gl.uniform1f(this.uniforms.u_pressed, this.pressed ? 1 : 0);
    gl.uniform1f(this.uniforms.u_trackStart, this.track.start*dpr);
    gl.uniform1f(this.uniforms.u_trackEnd, this.track.end*dpr);
    gl.uniform1f(this.uniforms.u_trackY, this.track.y*dpr);
    gl.uniform1f(this.uniforms.u_valueX, this.track.value*dpr);
    gl.uniform1f(this.uniforms.u_trackRadius, this.track.radius*dpr);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}
