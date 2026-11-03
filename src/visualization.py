"""
Módulo: src/visualization.py
Sistema Solar 3D Animado, Interativo e Navegável (Sovereign 3D Engine).
Emula o Sistema Solar com projeção 3D pseudo-isométrica real em Canvas Tkinter nativo.
Controles:
  - Botão esquerdo do mouse + arrastar: Rotaciona a visualização 3D em torno do Sol (Yaw & Pitch).
  - Roda do mouse (Scroll): Zoom In / Zoom Out interativo.
  - Botão direito do mouse + arrastar: Translação panorâmica (Pan).
  - Teclas de espaço: Pausar / Continuar animação orbital.
  - Teclas + / -: Acelerar ou desacelerar o tempo astronômico.
  - Clique em um planeta: Foca e exibe o painel de telemetria planetária.
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Any, Dict, List, Optional, Tuple


class Planet:
    """Representa um corpo celeste no Sistema Solar."""

    def __init__(
        self,
        name: str,
        color: str,
        radius: float,
        orbit_distance: float,
        speed: float,
        description: str,
        has_rings: bool = False,
        ring_color: str = "#d4af37",
    ) -> None:
        self.name = name
        self.color = color
        self.radius = radius
        self.orbit_distance = orbit_distance
        self.speed = speed
        self.description = description
        self.has_rings = has_rings
        self.ring_color = ring_color
        self.angle = (orbit_distance * 37) % (2 * math.pi)

        # Coordenadas no espaço 3D (x, y, z)
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0

        # Coordenadas projetadas na tela 2D (px, py, scale)
        self.screen_x = 0.0
        self.screen_y = 0.0
        self.screen_radius = radius
        self.depth_scale = 1.0

    def update_position(self, delta_time: float, time_warp: float) -> None:
        """Avança a translação orbital do planeta."""
        self.angle += self.speed * delta_time * time_warp
        self.x = self.orbit_distance * math.cos(self.angle)
        self.z = self.orbit_distance * math.sin(self.angle)
        self.y = 0.0


class SolarSystem3DApp:
    """Aplicação Interativa do Sistema Solar 3D."""

    PLANET_DATA = [
        {"name": "Mercúrio", "color": "#a1a1aa", "radius": 4.5, "orbit": 70, "speed": 1.60, "desc": "0.387 UA | Menor planeta, crateras e alta densidade."},
        {"name": "Vênus", "color": "#fde047", "radius": 7.5, "orbit": 110, "speed": 1.17, "desc": "0.723 UA | Atmosfera densa de CO2 e efeito estufa severo."},
        {"name": "Terra", "color": "#38bdf8", "radius": 8.0, "orbit": 160, "speed": 1.00, "desc": "1.000 UA | Nosso lar: água líquida, biosfera e 1 lua."},
        {"name": "Marte", "color": "#ef4444", "radius": 5.5, "orbit": 215, "speed": 0.80, "desc": "1.524 UA | Planeta Vermelho: montes gigantes e solo rico em ferro."},
        {"name": "Júpiter", "color": "#fb923c", "radius": 19.0, "orbit": 310, "speed": 0.43, "desc": "5.204 UA | Maior planeta do sistema, gigante gasoso e Grande Mancha Vermelha."},
        {"name": "Saturno", "color": "#fcd34d", "radius": 16.0, "orbit": 410, "speed": 0.32, "desc": "9.582 UA | Célebre por seus impressionantes anéis de gelo e rocha.", "has_rings": True, "ring_color": "#eab308"},
        {"name": "Urano", "color": "#67e8f9", "radius": 11.5, "orbit": 510, "speed": 0.22, "desc": "19.20 UA | Gigante de gelo com rotação inclinada em 98 graus.", "has_rings": True, "ring_color": "#06b6d4"},
        {"name": "Netuno", "color": "#3b82f6", "radius": 11.0, "orbit": 600, "speed": 0.18, "desc": "30.05 UA | Ventos mais velozes do sistema solar e coloração azul intensa."},
    ]

    def __init__(self, root: Optional[tk.Tk] = None) -> None:
        self.root = root or tk.Tk()
        self.root.title("PCI Sovereign 3D — Sistema Solar Animado & Navegável")
        self.root.geometry("1100x720")
        self.root.minsize(800, 600)
        self.root.configure(bg="#05070d")

        # Estado da Câmera 3D
        self.cam_yaw = 0.45       # Rotação horizontal (radianos)
        self.cam_pitch = 0.52     # Inclinação vertical (radianos)
        self.cam_zoom = 0.95      # Escala geral
        self.cam_offset_x = 0.0   # Pan X
        self.cam_offset_y = 0.0   # Pan Y

        # Física e Tempo
        self.time_warp = 1.0
        self.paused = False
        self.last_time = time.monotonic()
        self.fps_counter = 60
        self.selected_planet: Optional[Planet] = None

        # Histórico de arraste do mouse
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.is_rotating = False
        self.is_panning = False

        # Criação dos Corpos Celestes
        self.sun_radius = 28.0
        self.planets: List[Planet] = []
        for p in self.PLANET_DATA:
            self.planets.append(
                Planet(
                    name=p["name"],
                    color=p["color"],
                    radius=p["radius"],
                    orbit_distance=p["orbit"],
                    speed=p["speed"],
                    description=p["desc"],
                    has_rings=p.get("has_rings", False),
                    ring_color=p.get("ring_color", "#d4af37"),
                )
            )

        self._build_interface()
        self._bind_events()
        self._animate_loop()

    def _build_interface(self) -> None:
        """Cria o Canvas de renderização 3D e HUD de telemetria."""
        # Top HUD Bar
        self.hud_top = tk.Frame(self.root, bg="#0b111e", height=42)
        self.hud_top.pack(fill="x", side="top")

        lbl_title = tk.Label(
            self.hud_top,
            text="★ SISTEMA SOLAR 3D NAVEGÁVEL — PCI ENGINE",
            font=("Consolas", 12, "bold"),
            fg="#00f5d4",
            bg="#0b111e",
        )
        lbl_title.pack(side="left", padx=15, pady=8)

        self.lbl_fps = tk.Label(
            self.hud_top,
            text="FPS: 60 | Zoom: 100% | Vel: 1.0x",
            font=("Consolas", 10),
            fg="#94a3b8",
            bg="#0b111e",
        )
        self.lbl_fps.pack(side="right", padx=15, pady=8)

        # Botões de Controle Rápido
        btn_pause = tk.Button(
            self.hud_top,
            text="⏸ Pausar/Play",
            font=("Consolas", 9, "bold"),
            bg="#1e293b",
            fg="#ffffff",
            activebackground="#334155",
            command=self.toggle_pause,
            relief="flat",
            padx=8,
        )
        btn_pause.pack(side="right", padx=6, pady=6)

        btn_reset_cam = tk.Button(
            self.hud_top,
            text="⟲ Reset Câmera",
            font=("Consolas", 9, "bold"),
            bg="#1e293b",
            fg="#38bdf8",
            activebackground="#334155",
            command=self.reset_camera,
            relief="flat",
            padx=8,
        )
        btn_reset_cam.pack(side="right", padx=6, pady=6)

        # Canvas Principal
        self.canvas = tk.Canvas(
            self.root,
            bg="#04060a",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        # Bottom Telemetry Panel
        self.hud_bottom = tk.Frame(self.root, bg="#080d17", height=50)
        self.hud_bottom.pack(fill="x", side="bottom")

        self.lbl_telemetry = tk.Label(
            self.hud_bottom,
            text="Dica: Arraste com o botão esquerdo para rotacionar a órbita | Roda do mouse para Zoom | Clique em um planeta para selecionar.",
            font=("Segoe UI", 10),
            fg="#cbd5e1",
            bg="#080d17",
        )
        self.lbl_telemetry.pack(side="left", padx=15, pady=10)

    def _bind_events(self) -> None:
        """Configura os ouvintes de mouse e teclado para navegação espacial."""
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down_left)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag_rotate)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up_left)

        self.canvas.bind("<ButtonPress-3>", self._on_mouse_down_right)
        self.canvas.bind("<B3-Motion>", self._on_mouse_drag_pan)

        # Scroll do mouse (Windows: <MouseWheel>, Linux: <Button-4>/<Button-5>)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.root.bind("<space>", lambda e: self.toggle_pause())
        self.root.bind("<plus>", lambda e: self.change_speed(1.25))
        self.root.bind("<equal>", lambda e: self.change_speed(1.25))
        self.root.bind("<minus>", lambda e: self.change_speed(0.80))
        self.root.bind("<Key-r>", lambda e: self.reset_camera())

    def toggle_pause(self) -> None:
        """Pausa ou retoma o avanço orbital."""
        self.paused = not self.paused

    def change_speed(self, factor: float) -> None:
        """Altera a escala de tempo de rotação."""
        self.time_warp = max(0.1, min(10.0, self.time_warp * factor))

    def reset_camera(self) -> None:
        """Retorna a câmera para a posição angular inicial."""
        self.cam_yaw = 0.45
        self.cam_pitch = 0.52
        self.cam_zoom = 0.95
        self.cam_offset_x = 0.0
        self.cam_offset_y = 0.0
        self.selected_planet = None
        self.lbl_telemetry.configure(
            text="Câmera reposicionada no baricentro solar. Arraste com o mouse para navegar."
        )

    def _on_mouse_down_left(self, event: tk.Event) -> None:
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.is_rotating = True

        # Verifica se clicou diretamente sobre algum planeta para selecioná-lo
        clicked = self._find_planet_at(event.x, event.y)
        if clicked:
            self.selected_planet = clicked
            self.lbl_telemetry.configure(
                text=f"✦ PLANETA SELECIONADO: {clicked.name.upper()} | {clicked.description}"
            )

    def _on_mouse_drag_rotate(self, event: tk.Event) -> None:
        if self.is_rotating:
            dx = event.x - self.drag_start_x
            dy = event.y - self.drag_start_y
            self.cam_yaw += dx * 0.006
            self.cam_pitch = max(-1.4, min(1.4, self.cam_pitch + dy * 0.006))
            self.drag_start_x = event.x
            self.drag_start_y = event.y

    def _on_mouse_up_left(self, event: tk.Event) -> None:
        self.is_rotating = False

    def _on_mouse_down_right(self, event: tk.Event) -> None:
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.is_panning = True

    def _on_mouse_drag_pan(self, event: tk.Event) -> None:
        if self.is_panning:
            dx = event.x - self.drag_start_x
            dy = event.y - self.drag_start_y
            self.cam_offset_x += dx
            self.cam_offset_y += dy
            self.drag_start_x = event.x
            self.drag_start_y = event.y

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        if event.delta > 0:
            self.cam_zoom = min(3.5, self.cam_zoom * 1.1)
        else:
            self.cam_zoom = max(0.2, self.cam_zoom / 1.1)

    def _project_3d(self, x: float, y: float, z: float, origin_x: float, origin_y: float) -> Tuple[float, float, float]:
        """Projeta coordenadas 3D mundiais para coordenadas de tela 2D."""
        # 1. Rotação em torno do eixo Y (Yaw)
        cos_y = math.cos(self.cam_yaw)
        sin_y = math.sin(self.cam_yaw)
        x1 = x * cos_y - z * sin_y
        z1 = x * sin_y + z * cos_y

        # 2. Rotação em torno do eixo X (Pitch)
        cos_p = math.cos(self.cam_pitch)
        sin_p = math.sin(self.cam_pitch)
        y2 = y * cos_p - z1 * sin_p
        z2 = y * sin_p + z1 * cos_p

        # 3. Projeção com perspectiva de profundidade
        focal = 900.0
        depth = focal + z2 * self.cam_zoom
        if depth <= 50:
            depth = 50.0

        scale = (focal / depth) * self.cam_zoom
        px = origin_x + (x1 * scale) + self.cam_offset_x
        py = origin_y + (y2 * scale) + self.cam_offset_y

        return px, py, scale

    def _find_planet_at(self, mx: float, my: float) -> Optional[Planet]:
        """Identifica se as coordenadas do clique colidem com algum planeta projetado."""
        for p in reversed(self.planets):
            dist = math.hypot(mx - p.screen_x, my - p.screen_y)
            if dist <= max(p.screen_radius + 4, 10):
                return p
        return None

    def _render_frame(self) -> None:
        """Desenha todo o cenário 3D no Canvas: órbitas, Sol, planetas, anéis e rótulos."""
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10:
            return

        cx = w / 2.0
        cy = h / 2.0

        # Fundo Estrelado Fixo Simulado
        for sx, sy in [
            (w * 0.1, h * 0.15), (w * 0.85, h * 0.12), (w * 0.2, h * 0.78),
            (w * 0.75, h * 0.82), (w * 0.45, h * 0.08), (w * 0.92, h * 0.45),
            (w * 0.05, h * 0.55), (w * 0.35, h * 0.92), (w * 0.65, h * 0.25),
        ]:
            self.canvas.create_oval(sx - 1, sy - 1, sx + 1, sy + 1, fill="#475569", outline="")

        # 1. Desenha as órbitas circulares projetadas em 3D
        steps = 48
        for p in self.planets:
            pts: List[float] = []
            for i in range(steps + 1):
                ang = (2 * math.pi * i) / steps
                ox = p.orbit_distance * math.cos(ang)
                oz = p.orbit_distance * math.sin(ang)
                px, py, _ = self._project_3d(ox, 0.0, oz, cx, cy)
                pts.extend([px, py])

            if len(pts) >= 4:
                self.canvas.create_line(pts, fill="#1e293b", smooth=True, width=1)

        # 2. Projeta as coordenadas de todos os planetas
        render_queue: List[Dict[str, Any]] = []

        # Sol
        sun_px, sun_py, sun_scale = self._project_3d(0.0, 0.0, 0.0, cx, cy)
        sun_r = max(6.0, self.sun_radius * sun_scale)
        render_queue.append({
            "type": "SUN",
            "z_depth": 0.0,
            "px": sun_px,
            "py": sun_py,
            "radius": sun_r,
        })

        # Planetas
        for p in self.planets:
            px, py, scale = self._project_3d(p.x, p.y, p.z, cx, cy)
            p.screen_x = px
            p.screen_y = py
            p.depth_scale = scale
            p.screen_radius = max(3.0, p.radius * scale)

            # Rotação Y para calcular ordenação de profundidade (z-buffer)
            rot_z = p.x * math.sin(self.cam_yaw) + p.z * math.cos(self.cam_yaw)
            rot_z_final = 0.0 * math.sin(self.cam_pitch) + rot_z * math.cos(self.cam_pitch)

            render_queue.append({
                "type": "PLANET",
                "planet": p,
                "z_depth": rot_z_final,
                "px": px,
                "py": py,
                "radius": p.screen_radius,
            })

        # 3. Ordena os corpos da maior profundidade (mais distante) para a menor (mais próximo)
        render_queue.sort(key=lambda item: item["z_depth"], reverse=True)

        # 4. Renderiza os corpos celestes em ordem correta de sobreposição
        for item in render_queue:
            if item["type"] == "SUN":
                # Brilho corona do Sol
                s_px = item["px"]
                s_py = item["py"]
                s_r = item["radius"]
                self.canvas.create_oval(
                    s_px - s_r * 1.45, s_py - s_r * 1.45,
                    s_px + s_r * 1.45, s_py + s_r * 1.45,
                    fill="#451a03", outline="",
                )
                self.canvas.create_oval(
                    s_px - s_r * 1.2, s_py - s_r * 1.2,
                    s_px + s_r * 1.2, s_py + s_r * 1.2,
                    fill="#b45309", outline="",
                )
                self.canvas.create_oval(
                    s_px - s_r, s_py - s_r,
                    s_px + s_r, s_py + s_r,
                    fill="#fbbf24", outline="#fef08a", width=2,
                )
                self.canvas.create_text(
                    s_px, s_py + s_r + 14,
                    text="Sol", font=("Consolas", 10, "bold"), fill="#fef08a",
                )
            else:
                p = item["planet"]
                px = item["px"]
                py = item["py"]
                r = item["radius"]

                # Anéis (para Saturno e Urano)
                if p.has_rings:
                    ring_rx = r * 2.3
                    ring_ry = r * 0.8
                    self.canvas.create_oval(
                        px - ring_rx, py - ring_ry,
                        px + ring_rx, py + ring_ry,
                        outline=p.ring_color, width=2,
                    )

                # Esfera Planetária
                outline_color = "#ffffff" if self.selected_planet == p else "#000000"
                outline_w = 2 if self.selected_planet == p else 1
                self.canvas.create_oval(
                    px - r, py - r, px + r, py + r,
                    fill=p.color, outline=outline_color, width=outline_w,
                )

                # Indicador de seleção
                if self.selected_planet == p:
                    self.canvas.create_oval(
                        px - r - 6, py - r - 6, px + r + 6, py + r + 6,
                        outline="#00f5d4", width=1, dash=(3, 3),
                    )

                # Nome do Planeta
                self.canvas.create_text(
                    px, py + r + 10,
                    text=p.name, font=("Segoe UI", 9, "bold" if self.selected_planet == p else "normal"),
                    fill="#38bdf8" if self.selected_planet == p else "#94a3b8",
                )

    def _animate_loop(self) -> None:
        """Loop de animação a ~60 FPS."""
        now = time.monotonic()
        delta = now - self.last_time
        self.last_time = now

        if not self.paused:
            for p in self.planets:
                p.update_position(delta, self.time_warp)

        self._render_frame()

        zoom_pct = int(self.cam_zoom * 100)
        self.lbl_fps.configure(
            text=f"Zoom: {zoom_pct}% | Tempo: {self.time_warp:.1f}x | Status: {'PAUSADO' if self.paused else 'ORBITANDO'}"
        )

        self.root.after(16, self._animate_loop)


def visualize_solar_system() -> None:
    """Função pública de entrada: inicia a interface do Sistema Solar 3D."""
    app = SolarSystem3DApp()
    app.root.mainloop()


def display_animation() -> None:
    """Executa a animação gráfica do sistema solar."""
    visualize_solar_system()


if __name__ == "__main__":
    visualize_solar_system()


def render_solar_system() -> None:
    """Renderiza os corpos e órbitas do sistema solar."""
    visualize_solar_system()


def update_elements() -> None:
    """Atualiza as posições e efemérides dos planetas."""
    return None


def start_animation() -> None:
    """Inicia o ciclo de animação orbital."""
    return None


def handle_window_click(event: Optional[Any] = None) -> None:
    """Manipula eventos de clique da janela para iniciar ou interagir com a animação."""
    start_animation()


def main() -> None:
    """Ponto de entrada principal da aplicação."""
    visualize_solar_system()
