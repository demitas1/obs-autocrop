// obs-autocrop-perspective v1
// obs-autocrop: 射影補正（台形補正）シェーダー（PLAN.md 15章、docs/PLAN_perspective.md 5章）
// 1 行目は版の印。ツールは OBS 側の shader_text の 1 行目と比べ、古ければフィルタを作り直す（本文を変えたら版を上げる）。
// obs-shaderfilter 2.6.1（フィルタの種別 ID: shader_filter）で使う。
//
// 出力の UV (u, v) を、出力 → 入力の射影行列 M（= H^-1、M[2][2] = 1 に正規化）で入力の UV に写し、
// 入力テクスチャから色を取る:
//     [x, y, w]^T = M * [u, v, 1]^T,  入力の UV = (x / w, y / w)
// UV は画素中心が (i + 0.5) / W の正規化座標（PLAN.md 15.2 の 1）。
// 行列の値は websocket の SetSourceFilterSettings で uniform 名（m00〜m22）をキーにして設定する。
// 既定値は単位行列（パススルー）。expand_* は 0 のまま使う（uv_scale = 1, uv_offset = 0）。

uniform float m00 = 1.0;
uniform float m01 = 0.0;
uniform float m02 = 0.0;
uniform float m10 = 0.0;
uniform float m11 = 1.0;
uniform float m12 = 0.0;
uniform float m20 = 0.0;
uniform float m21 = 0.0;
uniform float m22 = 1.0;
uniform bool outside_black = false;

float4 mainImage(VertData v_in) : TARGET
{
	float2 uv = v_in.uv;
	float x = m00 * uv.x + m01 * uv.y + m02;
	float y = m10 * uv.x + m11 * uv.y + m12;
	float w = m20 * uv.x + m21 * uv.y + m22;
	float4 outside = outside_black ? float4(0.0, 0.0, 0.0, 1.0) : float4(0.0, 0.0, 0.0, 0.0);
	if (w <= 0.0)
		return outside;
	float2 src = float2(x, y) / w;
	if (src.x < 0.0 || src.x > 1.0 || src.y < 0.0 || src.y > 1.0)
		return outside;
	return image.Sample(textureSampler, src);
}
