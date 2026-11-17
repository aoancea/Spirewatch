"""GLSL shaders used by the game (world lighting, sky, water)."""
from __future__ import annotations

from panda3d.core import Shader

_VERSION = "#version 140\n"

# ---------------------------------------------------------------------------------
# World: flat-shaded vertex colours, hemispheric ambient + sun + point lights + fog
# ---------------------------------------------------------------------------------
WORLD_VERT = _VERSION + """
uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;
uniform float osg_FrameTime;
uniform float u_flame;
in vec4 p3d_Vertex;
in vec3 p3d_Normal;
in vec4 p3d_Color;
in vec2 p3d_MultiTexCoord0;
out vec3 v_wpos;
out vec3 v_normal;
out vec4 v_color;
out vec2 v_uv;
void main() {
    vec4 lp = p3d_Vertex;
    if (u_flame > 0.0) {
        float t = osg_FrameTime;
        float ph = lp.x * 1.7 + lp.z * 1.3;
        float k = u_flame * clamp(lp.y * 0.6, 0.0, 1.0);
        lp.x += sin(t * 9.0 + lp.y * 3.0 + ph) * 0.07 * k;
        lp.z += cos(t * 7.5 + lp.y * 2.5 + ph) * 0.07 * k;
        lp.y += sin(t * 11.0 + ph) * 0.06 * k;
    }
    vec4 wp = p3d_ModelMatrix * lp;
    v_wpos = wp.xyz;
    v_normal = mat3(p3d_ModelMatrix) * p3d_Normal;
    v_color = p3d_Color;
    v_uv = p3d_MultiTexCoord0;
    gl_Position = p3d_ModelViewProjectionMatrix * lp;
}
"""

WORLD_FRAG = _VERSION + """
uniform sampler2D p3d_Texture0;
uniform sampler2D u_detail_tex;
uniform vec4 p3d_ColorScale;
uniform vec3 u_sun_dir;
uniform vec3 u_sun_color;
uniform vec3 u_amb_sky;
uniform vec3 u_amb_ground;
uniform vec3 u_fog_color;
uniform vec2 u_fog_range;
uniform vec3 u_cam_pos;
uniform vec4 u_lights_pos[8];
uniform vec4 u_lights_col[8];
uniform int u_num_lights;
uniform float u_emissive;
uniform float u_detail;
uniform float u_rim;
in vec3 v_wpos;
in vec3 v_normal;
in vec4 v_color;
in vec2 v_uv;
out vec4 o_color;
void main() {
    vec4 base = texture(p3d_Texture0, v_uv) * v_color * p3d_ColorScale;
    if (base.a < 0.02) discard;
    vec3 n = normalize(v_normal);
    if (u_detail > 0.0) {
        float d1 = texture(u_detail_tex, v_wpos.xz * 0.23).r;
        float d2 = texture(u_detail_tex, v_wpos.xz * 0.031 + vec2(0.37, 0.71)).r;
        base.rgb *= mix(1.0, 0.80 + 0.26 * d1 + 0.18 * (d2 - 0.5), u_detail);
    }
    float ndl = max(dot(n, u_sun_dir), 0.0);
    float hemi = n.y * 0.5 + 0.5;
    vec3 light = mix(u_amb_ground, u_amb_sky, hemi) + u_sun_color * ndl;
    for (int i = 0; i < u_num_lights; i++) {
        vec3 d = u_lights_pos[i].xyz - v_wpos;
        float dist = length(d);
        float att = clamp(1.0 - dist / u_lights_pos[i].w, 0.0, 1.0);
        att *= att;
        float nd = max(dot(n, d / max(dist, 0.001)), 0.0) * 0.75 + 0.25;
        light += u_lights_col[i].rgb * (u_lights_col[i].a * att * nd);
    }
    vec3 vdir = normalize(u_cam_pos - v_wpos);
    float rim = pow(1.0 - max(dot(n, vdir), 0.0), 3.0) * u_rim;
    vec3 col = base.rgb * mix(light, vec3(1.0), clamp(u_emissive, 0.0, 1.0));
    col += base.rgb * max(u_emissive - 1.0, 0.0);
    col += rim * (u_sun_color * 0.6 + u_amb_sky * 0.6);
    float dist = length(v_wpos - u_cam_pos);
    float fog = clamp((dist - u_fog_range.x) / max(u_fog_range.y - u_fog_range.x, 1.0), 0.0, 1.0);
    col = mix(col, u_fog_color, fog * fog * (3.0 - 2.0 * fog));
    o_color = vec4(col, base.a);
}
"""

# ---------------------------------------------------------------------------------
# Sky dome (centred on the camera)
# ---------------------------------------------------------------------------------
SKY_VERT = _VERSION + """
uniform mat4 p3d_ModelViewProjectionMatrix;
in vec4 p3d_Vertex;
out vec3 v_dir;
void main() {
    v_dir = p3d_Vertex.xyz;
    gl_Position = p3d_ModelViewProjectionMatrix * p3d_Vertex;
}
"""

SKY_FRAG = _VERSION + """
uniform vec3 u_sky_zenith;
uniform vec3 u_sky_horizon;
uniform vec3 u_fog_color;
uniform vec3 u_sky_sun;
uniform vec3 u_sun_color;
uniform vec3 u_moon_dir;
uniform float u_night;
uniform float u_underground;
in vec3 v_dir;
out vec4 o_color;
float hash(vec3 p) {
    p = fract(p * 0.3183099 + vec3(0.1, 0.2, 0.3));
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
void main() {
    vec3 d = normalize(v_dir);
    float h = d.y;
    vec3 col = mix(u_sky_horizon, u_sky_zenith, pow(clamp(h, 0.0, 1.0), 0.5));
    col = mix(col, u_fog_color, smoothstep(0.12, -0.05, h));
    float sd = max(dot(d, u_sky_sun), 0.0);
    col += u_sun_color * (smoothstep(0.9985, 0.9993, sd) * 4.0 + pow(sd, 10.0) * 0.22 + pow(sd, 90.0) * 0.4);
    float md = max(dot(d, u_moon_dir), 0.0);
    col += vec3(0.85, 0.9, 1.0) * (smoothstep(0.9990, 0.9995, md) * 0.9 + pow(md, 50.0) * 0.07) * u_night;
    vec3 sp = floor(d * 300.0);
    float s = hash(sp);
    float tw = 0.6 + 0.4 * hash(sp + vec3(7.0));
    col += vec3(step(0.9972, s) * u_night * u_night * u_night * smoothstep(0.02, 0.25, h) * tw);
    col = mix(col, vec3(0.02, 0.015, 0.012), u_underground);
    o_color = vec4(col, 1.0);
}
"""

# ---------------------------------------------------------------------------------
# Water (mesh vertices are in world space, node has identity rotation)
# ---------------------------------------------------------------------------------
WATER_VERT = _VERSION + """
uniform mat4 p3d_ModelViewProjectionMatrix;
uniform mat4 p3d_ModelMatrix;
uniform float osg_FrameTime;
in vec4 p3d_Vertex;
out vec3 v_wpos;
out vec3 v_normal;
void main() {
    vec4 wp = p3d_ModelMatrix * p3d_Vertex;
    float t = osg_FrameTime;
    float a1 = wp.x * 0.35 + t * 1.1;
    float a2 = wp.z * 0.42 - t * 0.8 + wp.x * 0.1;
    float a3 = (wp.x + wp.z) * 0.9 + t * 1.9;
    float disp = sin(a1) * 0.035 + sin(a2) * 0.03 + sin(a3) * 0.012;
    float dx = cos(a1) * 0.035 * 0.35 + cos(a2) * 0.03 * 0.1 + cos(a3) * 0.012 * 0.9;
    float dz = cos(a2) * 0.03 * 0.42 + cos(a3) * 0.012 * 0.9;
    v_normal = normalize(vec3(-dx, 1.0, -dz));
    v_wpos = vec3(wp.x, wp.y + disp, wp.z);
    vec4 lp = p3d_Vertex;
    lp.y += disp;
    gl_Position = p3d_ModelViewProjectionMatrix * lp;
}
"""

WATER_FRAG = _VERSION + """
uniform sampler2D u_depth_tex;
uniform vec4 u_depth_rect;
uniform vec3 u_cam_pos;
uniform vec3 u_sun_dir;
uniform vec3 u_sun_color;
uniform vec3 u_amb_sky;
uniform vec3 u_sky_horizon;
uniform vec3 u_fog_color;
uniform vec2 u_fog_range;
uniform float osg_FrameTime;
in vec3 v_wpos;
in vec3 v_normal;
out vec4 o_color;
void main() {
    vec2 uv = (v_wpos.xz - u_depth_rect.xy) / u_depth_rect.zw;
    float depth = texture(u_depth_tex, uv).r * 4.0;
    float t = osg_FrameTime;
    vec3 shallow = vec3(0.24, 0.66, 0.68);
    vec3 deep = vec3(0.05, 0.24, 0.40);
    float dk = clamp(depth / 1.1, 0.0, 1.0);
    vec3 base = mix(shallow, deep, sqrt(dk));
    vec3 n = normalize(v_normal + vec3(sin(v_wpos.z * 2.3 + t * 2.0), 0.0, cos(v_wpos.x * 2.1 - t * 1.7)) * 0.06);
    vec3 v = normalize(u_cam_pos - v_wpos);
    float fres = pow(1.0 - max(dot(n, v), 0.0), 4.0);
    vec3 lightc = u_amb_sky * 0.9 + u_sun_color * (max(dot(n, u_sun_dir), 0.0) * 0.55 + 0.15);
    vec3 col = base * lightc + fres * u_sky_horizon * 0.6;
    vec3 hv = normalize(u_sun_dir + v);
    col += u_sun_color * pow(max(dot(n, hv), 0.0), 180.0) * 1.2;
    float band = 0.5 + 0.5 * sin(depth * 22.0 - t * 2.2);
    float foam = smoothstep(0.30, 0.02, depth) * (0.35 + 0.65 * band);
    col = mix(col, vec3(0.93, 0.92, 0.88) * (u_amb_sky + u_sun_color * 0.8), clamp(foam * 0.7, 0.0, 0.8));
    float alpha = clamp(mix(0.55, 0.92, dk) + foam * 0.3, 0.0, 0.97);
    if (depth <= 0.001) alpha = 0.0;
    float dist = length(v_wpos - u_cam_pos);
    float fog = clamp((dist - u_fog_range.x) / max(u_fog_range.y - u_fog_range.x, 1.0), 0.0, 1.0);
    fog = fog * fog * (3.0 - 2.0 * fog);
    col = mix(col, u_fog_color, fog);
    o_color = vec4(col, alpha);
}
"""

_cache: dict[str, Shader] = {}


def _make(name: str, vert: str, frag: str) -> Shader:
    if name not in _cache:
        _cache[name] = Shader.make(Shader.SL_GLSL, vert, frag)
    return _cache[name]


def world_shader() -> Shader:
    return _make("world", WORLD_VERT, WORLD_FRAG)


def sky_shader() -> Shader:
    return _make("sky", SKY_VERT, SKY_FRAG)


def water_shader() -> Shader:
    return _make("water", WATER_VERT, WATER_FRAG)
