export const vertexShader = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
  v_uv = a_pos * 0.5 + 0.5;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

export const fragmentShader = `
precision highp float;
uniform sampler2D u_bg;
uniform vec2 u_res;
uniform vec2 u_center;
uniform vec2 u_size;
uniform vec2 u_bgScale;
uniform vec2 u_bgOffset;
uniform float u_blurAmount;
uniform float u_radius;
uniform float u_zRadius;
uniform float u_refract;
uniform float u_chroma;
uniform float u_edgeHL;
uniform float u_specular;
uniform float u_fresnel;
uniform float u_brightness;
uniform float u_saturation;
uniform float u_shadowAlpha;
uniform float u_shadowSpread;
uniform float u_darkTint;
uniform float u_bevelMode;
uniform float u_button;
uniform float u_pressed;
uniform float u_distortion;
uniform float u_tintStrength;
uniform float u_tint;
uniform float u_opacity;
uniform float u_sampleBackground;
uniform float u_materialMorph;
uniform vec3 u_tintColor;
uniform vec3 u_trackBaseColor;
uniform vec3 u_trackFillColor;
uniform float u_trackStart;
uniform float u_trackEnd;
uniform float u_trackY;
uniform float u_valueX;
uniform float u_trackRadius;
varying vec2 v_uv;

float rrSDF(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + vec2(r);
  return min(max(q.x, q.y), 0.0) + length(max(q, vec2(0.0))) - r;
}
float lensHeight(float inside, float zR) {
  if (inside <= 0.0) return 0.0;
  if (inside >= zR) return zR;
  return sqrt(inside * (2.0 * zR - inside));
}
float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}
vec2 toBackgroundUV(vec2 screenUV) {
  return screenUV * u_bgScale + u_bgOffset;
}
vec3 sampleBlur(vec2 uv, float amount) {
  vec2 px = vec2(1.0) / u_res * u_bgScale;
  float b = amount * 18.0;
  vec3 color = texture2D(u_bg, uv).rgb * 0.36;
  color += texture2D(u_bg, uv + px * vec2( b, 0.0)).rgb * 0.10;
  color += texture2D(u_bg, uv + px * vec2(-b, 0.0)).rgb * 0.10;
  color += texture2D(u_bg, uv + px * vec2(0.0,  b)).rgb * 0.10;
  color += texture2D(u_bg, uv + px * vec2(0.0, -b)).rgb * 0.10;
  color += texture2D(u_bg, uv + px * vec2( b,  b) * .72).rgb * .06;
  color += texture2D(u_bg, uv + px * vec2(-b,  b) * .72).rgb * .06;
  color += texture2D(u_bg, uv + px * vec2( b, -b) * .72).rgb * .06;
  color += texture2D(u_bg, uv + px * vec2(-b, -b) * .72).rgb * .06;
  return color;
}
vec3 spectralSample(vec2 base, vec2 axis, float blurAmount, float mixAmount) {
  vec3 neutral = sampleBlur(base, blurAmount);
  vec3 red = sampleBlur(base + axis * 3.0, blurAmount) * vec3(1.0, 0.0, 0.0);
  vec3 orange = sampleBlur(base + axis * 2.0, blurAmount) * vec3(1.0, 0.45, 0.0);
  vec3 yellow = sampleBlur(base + axis, blurAmount) * vec3(1.0, 1.0, 0.0);
  vec3 green = sampleBlur(base, blurAmount) * vec3(0.0, 1.0, 0.0);
  vec3 cyan = sampleBlur(base - axis, blurAmount) * vec3(0.0, 1.0, 1.0);
  vec3 blue = sampleBlur(base - axis * 2.0, blurAmount) * vec3(0.0, 0.0, 1.0);
  vec3 violet = sampleBlur(base - axis * 3.0, blurAmount) * vec3(0.5, 0.0, 1.0);
  vec3 spectrum = (red + orange + yellow + green + cyan + blue + violet)
    / vec3(3.5, 3.45, 3.0);
  return mix(neutral, spectrum, mixAmount);
}
vec3 adjustColor(vec3 color) {
  color += u_brightness;
  float gray = dot(color, vec3(.299,.587,.114));
  return clamp(mix(vec3(gray), color, 1.0 + u_saturation), 0.0, 1.0);
}
float roundedTrackMask(vec2 point, float startX, float endX, float centerY, float radius) {
  float safeEnd = max(endX, startX);
  vec2 segment = vec2(clamp(point.x, startX, safeEnd), centerY);
  return 1.0 - smoothstep(radius - 1.0, radius + 1.0, length(point - segment));
}
void main() {
  vec2 screenPx = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y);
  vec2 localPx = screenPx - u_center;
  vec2 halfSize = u_size * .5;
  float radius = min(u_radius, min(halfSize.x, halfSize.y));
  float sdf = rrSDF(localPx, halfSize, radius);
  if (sdf > 0.0) {
    float d = max(sdf - 1.0, 0.0);
    float shadow = exp(-d*d / max(u_shadowSpread*u_shadowSpread, 1.0)) * u_shadowAlpha;
    if (shadow < .002) discard;
    gl_FragColor = vec4(0.,0.,0.,shadow);
    return;
  }
  float mask = 1.0 - smoothstep(-1.8, .45, sdf);
  float inside = -sdf;
  float edge = 1.0 - smoothstep(0.0, u_zRadius * 1.12, inside);
  float core = smoothstep(u_zRadius * .55, u_zRadius * 1.9, inside);
  float e = 2.0;
  float dR = -rrSDF(localPx + vec2(e,0.), halfSize, radius);
  float dL = -rrSDF(localPx - vec2(e,0.), halfSize, radius);
  float dU = -rrSDF(localPx + vec2(0.,e), halfSize, radius);
  float dD = -rrSDF(localPx - vec2(0.,e), halfSize, radius);
  float hC = lensHeight(inside, u_zRadius);
  float hR = lensHeight(dR, u_zRadius);
  float hL = lensHeight(dL, u_zRadius);
  float hU = lensHeight(dU, u_zRadius);
  float hD = lensHeight(dD, u_zRadius);
  float dome = mix(
    1.0,
    .22 + .78 * smoothstep(-.9, .9, -localPx.y / max(halfSize.y, 1.0)),
    u_bevelMode
  );
  hC *= dome;
  hR *= dome;
  hL *= dome;
  hU *= dome;
  hD *= dome;
  vec2 hGrad = vec2(hR-hL, hU-hD)/(2.0*e);
  vec3 normal = normalize(vec3(-hGrad,1.));
  float depth = smoothstep(0.0, u_zRadius, inside);
  float ior = mix(1.36, 1.58, clamp(u_refract, 0.0, 1.2));
  float refrPow = 1.0 - 1.0 / ior;
  float press = u_button * u_pressed;
float thickness = hC * mix(2.0, 1.1, press);
  float thickNorm = thickness / max(u_zRadius * 2.0, 1.0);
  vec2 centerDir = -localPx / max(halfSize, vec2(1.0));
  vec2 refrPx = (hGrad * refrPow * 2.15 + centerDir * edge * .28)
    * u_refract * (26.0 + u_zRadius * .22);
  refrPx += hGrad * refrPow * thickNorm * u_refract * 22.0;
  refrPx *= mix(edge, max(edge, .16 * (1.0 - core)), u_blurAmount);
  refrPx *= mix(1.0, .74, press);

  vec2 noisePoint = localPx * .08;
  vec2 micro = (vec2(hash(noisePoint), hash(noisePoint + vec2(37.0))) - .5)
    * u_distortion * 4.0;
  vec2 refractedScreenPx = screenPx + refrPx + micro;
  vec2 trackSamplePx = mix(refractedScreenPx, screenPx + refrPx * 1.8 + micro, u_button);
  float trackRadius = u_trackRadius;
  float fullTrack = roundedTrackMask(trackSamplePx, u_trackStart, u_trackEnd, u_trackY, trackRadius);
  float fillTrack = roundedTrackMask(
    trackSamplePx,
    u_trackStart,
    u_valueX,
    u_trackY,
    trackRadius
  );
  float caShift = u_chroma * 18.0 * (edge * .7 + .3) * 2.0;
  float redTrack = roundedTrackMask(trackSamplePx + normal.xy * caShift, u_trackStart, u_trackEnd, u_trackY, trackRadius);
  float blueTrack = roundedTrackMask(trackSamplePx - normal.xy * caShift, u_trackStart, u_trackEnd, u_trackY, trackRadius);
  vec3 neutralTrack = mix(u_trackBaseColor, u_trackFillColor, fillTrack);
  vec3 trackColor = neutralTrack;
  trackColor.r *= mix(1.0, redTrack, min(1.0, u_chroma * 4.0));
  trackColor.b *= mix(1.0, blueTrack, min(1.0, u_chroma * 4.0));

  float vertical = clamp(.5 - localPx.y / max(u_size.y, 1.0), 0.0, 1.0);
  float topSheen = smoothstep(.42, .98, vertical);
  float lowerBody = smoothstep(.0, .58, vertical);
  vec2 pxToUV = vec2(1.0, -1.0) / u_res;
  vec2 backgroundUV = toBackgroundUV(v_uv + refrPx * pxToUV);
  float chromaSpread = u_chroma * 34.0 * (.35 + edge * .95);
  vec2 chromaAxis = normalize(normal.xy + vec2(.0001))
    * chromaSpread * pxToUV * u_bgScale;
  float prismMix = min(.32, u_chroma * 3.6 * (.25 + edge));
  vec3 sampledBackground = spectralSample(
    backgroundUV,
    chromaAxis,
    u_blurAmount,
    prismMix
  );
  sampledBackground = adjustColor(sampledBackground);

  float glassLight = .032 + depth * .018 + lowerBody * .018 + topSheen * .055;
  vec3 neutralGlass = vec3(glassLight, glassLight + .004, glassLight + .01);
  vec3 color = mix(neutralGlass, sampledBackground, u_sampleBackground);
  color = mix(color, color * u_tintColor, u_tintStrength);
  color *= 1.0 - u_darkTint * (.62 + edge * .38);
  if (u_tint >= 0.0) {
    color = mix(color, vec3(1.0), clamp(u_tint, 0.0, 1.0) * .5);
  } else {
    color = mix(color, vec3(0.0), clamp(-u_tint, 0.0, 1.0) * .72);
  }
  float trackTransmission = clamp(
    .74 + core * .25 + topSheen * .07 - edge * .12,
    .58,
    1.04
  );
  vec3 transmittedTrack = trackColor * trackTransmission;
  float surfaceVeil = (.07 + edge * .2) * (.55 + u_darkTint);
  transmittedTrack = mix(transmittedTrack, neutralGlass, surfaceVeil);
  color = mix(color, transmittedTrack, fullTrack * .94);

  vec3 lightDir = normalize(vec3(-.35, -.58, .74));
  vec3 viewDir = vec3(0.0, 0.0, 1.0);
  vec3 halfDir = normalize(lightDir + viewDir);
  float spec = pow(max(dot(normal, halfDir), 0.0), 72.0) * u_specular;
  float fres = pow(1.0 - clamp(normal.z, 0.0, 1.0), 3.2) * u_fresnel;
  float rim = edge * u_edgeHL * .78;
  float stroke = smoothstep(-4.0, -1.4, sdf)
    * (1.0 - smoothstep(-1.2, 0.0, sdf));
  float topBias = .5 + .5 * (-localPx.y / max(halfSize.y, 1.0));
  vec3 rimColor = vec3(1.0, .98, .92)
    * (rim * (.09 + topBias * .08) + stroke * u_edgeHL * .18);
  vec3 fresColor = vec3(.90, .96, 1.0) * fres * .16;
  color += rimColor + fresColor + vec3(spec * .28);
  color = mix(color, vec3(1.0), stroke * .05);
  vec3 staticPill = mix(
    vec3(.93, .93, .945),
    vec3(1.0),
    smoothstep(-.9, .4, -localPx.y / max(halfSize.y, 1.0))
  );
  float materialEase = smoothstep(0.0, 1.0, u_materialMorph);
  color = mix(staticPill, color, materialEase);
  float materialOpacity = mix(1.0, u_opacity, materialEase);
  gl_FragColor = vec4(color, mask * materialOpacity);
}`;
