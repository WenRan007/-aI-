"""Shared FFmpeg filters for exports and the GIF effect gallery."""
EFFECTS = ["烟雾纹理", "星点闪烁", "柔和亮度", "轻微对比", "轻微饱和", "暖色调", "冷色调", "暗角", "细微噪点", "锐化", "高光", "透明边框", "斜线纹理", "色相偏移", "电影色调"]


def build_effect_filter(name, opacity):
    a = max(0.0, min(100.0, float(opacity))) / 100
    if a == 0:
        return "null"
    if name == "暗角":
        return f"vignette=PI*{a:.4f}/5"
    if name == "细微噪点":
        return f"noise=alls={max(1, round(6*a))}:allf=t"
    if name == "锐化":
        return f"unsharp=5:5:{a*.6:.4f}"
    if name == "暖色调":
        return f"colorbalance=rs={a*.08:.4f}:gs={a*.02:.4f}:bs={-a*.06:.4f}"
    if name == "冷色调":
        return f"colorbalance=rs={-a*.06:.4f}:gs={a*.02:.4f}:bs={a*.08:.4f}"
    if name == "斜线纹理":
        return f"drawgrid=w=iw/7:h=ih/10:t=1:c=white@{a*.15:.4f}"
    if name == "透明边框":
        return f"drawbox=x=iw*.012:y=ih*.012:w=iw*.976:h=ih*.976:t=4:c=white@{a*.35:.4f}"
    if name == "星点闪烁":
        stars = []
        for i, (x, y) in enumerate(((.2, .18), (.8, .3), (.15, .72), (.85, .85))):
            stars.append(f"drawbox=x=iw*{x}:y=ih*{y}:w=3:h=3:t=fill:c=white@{a:.4f}:enable='lt(mod(t+{i*.37:.2f},1.6),0.65)'")
        return ",".join(stars)
    if name == "烟雾纹理":
        return f"drawbox=x=0:y=ih*.45:w=iw:h=ih*.1:t=fill:c=white@{a*.06:.4f}"
    if name == "轻微对比":
        return f"eq=contrast={1+a*.08:.4f}"
    if name == "轻微饱和":
        return f"eq=saturation={1+a*.15:.4f}"
    if name == "高光":
        return f"eq=brightness={a*.03:.4f}"
    if name == "色相偏移":
        return f"hue=h={a*6:.4f}"
    if name == "电影色调":
        return f"eq=contrast={1+a*.06:.4f}:saturation={1-a*.12:.4f}"
    return f"eq=brightness={a*.02:.4f}"
