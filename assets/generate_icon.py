"""
Gerador de Ícone Profissional do PCI Master System v2.0 (PCI Sovereign Code Engine).
Estética Dark/Cyber: Escudo Neon Cyan (#00e5ff), Núcleo de Processamento Soberano e Raios Dourados/Ciano.
Gera ícones em multi-resoluções (16x16 até 256x256) no formato .ico e .png para desktop e executáveis.
"""

from pathlib import Path
from PIL import Image, ImageDraw


def generate_pci_icon(output_dir: Path | str = "assets") -> Path:
    out_path = Path(output_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    ico_file = out_path / "pci_icon.ico"
    png_file = out_path / "pci_icon.png"

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Fundo Circular / Escudo Dark Obsidian com Borda Neon Cyan
    # Centro e raio
    cx, cy = size // 2, size // 2
    r_outer = 118
    r_inner = 110

    # Círculo externo brilhante (glow cyan)
    draw.ellipse(
        [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
        fill=(10, 16, 26, 255),
        outline=(0, 229, 255, 255),
        width=5,
    )

    # Anel intermediário sutil
    draw.ellipse(
        [cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner],
        outline=(0, 128, 180, 180),
        width=2,
    )

    # 2. Escudo Hexagonal Soberano Central
    shield_pts = [
        (cx, cy - 80),        # Topo central
        (cx + 65, cy - 40),   # Topo direito
        (cx + 65, cy + 30),   # Meio direito
        (cx, cy + 85),        # Ponta inferior
        (cx - 65, cy + 30),   # Meio esquerdo
        (cx - 65, cy - 40),   # Topo esquerdo
    ]
    draw.polygon(shield_pts, fill=(15, 23, 42, 240), outline=(0, 255, 200, 255))

    # Borda interna do escudo
    inner_shield_pts = [
        (cx, cy - 70),
        (cx + 53, cy - 35),
        (cx + 53, cy + 25),
        (cx, cy + 72),
        (cx - 53, cy + 25),
        (cx - 53, cy - 35),
    ]
    draw.polygon(inner_shield_pts, fill=(18, 30, 49, 255), outline=(0, 229, 255, 160))

    # 3. Raio de Processamento Soberano (Lightning Bolt) Centralizado
    # Estilizado em Dourado / Neon Cyan
    bolt_pts = [
        (cx + 8, cy - 50),
        (cx - 24, cy - 5),
        (cx - 2, cy - 5),
        (cx - 16, cy + 45),
        (cx + 26, cy - 5),
        (cx + 4, cy - 5),
    ]
    # Sombra/Glow do raio
    draw.polygon(bolt_pts, fill=(0, 255, 220, 255), outline=(255, 255, 255, 255))

    # Núcleo brilhante do raio
    inner_bolt = [
        (cx + 6, cy - 44),
        (cx - 18, cy - 7),
        (cx, cy - 7),
        (cx - 10, cy + 35),
        (cx + 20, cy - 7),
        (cx + 3, cy - 7),
    ]
    draw.polygon(inner_bolt, fill=(255, 255, 255, 230))

    # 4. Pequenos pontos de nós distribuídos (Tailscale / Horde)
    node_points = [
        (cx - 85, cy - 15),
        (cx + 85, cy - 15),
        (cx - 50, cy - 75),
        (cx + 50, cy - 75),
        (cx, cy + 98),
    ]
    for px, py in node_points:
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(0, 255, 200, 255), outline=(255, 255, 255, 255), width=1)

    # 5. Salva PNG master
    img.save(png_file, format="PNG")

    # 6. Salva ICO multi-resolução para Windows
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_file, format="ICO", sizes=sizes)

    return ico_file


if __name__ == "__main__":
    generated = generate_pci_icon("C:/Lab/013 - Sovereign-Code-Engine/assets")
    print(f"Ícone gerado com sucesso: {generated}")
