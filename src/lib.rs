//! Browser WASI reactor. The engine runs entirely inside the browser.
#![cfg(any(target_arch = "wasm32", test))]

use clr::{
    focus,
    platform_api::{DrawTarget, TriangleList},
    render::{Renderer, ATLAS_SIZE},
    unity_api, Vm,
};
use serde::Deserialize;
use serde_json::{json, value::RawValue, Value};
use std::cell::RefCell;
use std::collections::VecDeque;

const STEP: f32 = 1.0 / 30.0;
const HEIGHT: f32 = 600.0;
// Ignore legacy resolution requests during the first 120 engine frames.
const STARTUP_RESOLUTION_GUARD_FRAMES: u64 = 120;

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Event {
    Move {
        x: f32,
        y: f32,
    },
    Button {
        x: f32,
        y: f32,
        button: i32,
        down: bool,
    },
    Wheel {
        x: f32,
        y: f32,
        delta: f32,
    },
    Key {
        key: String,
        right: bool,
        down: bool,
        repeat: bool,
    },
    Navigate {
        action: String,
    },
    Scroll {
        x: f32,
        y: f32,
    },
    Text {
        value: Option<String>,
    },
    Profile {
        online: bool,
        locked: u8,
        reload: bool,
    },
    Lease {
        granted: bool,
    },
    SaveLabel {
        message: String,
    },
    Notice {
        message: String,
    },
    Import {
        slot: usize,
    },
    Export {
        slot: Option<usize>,
    },
    #[cfg(feature = "audit")]
    Audit {
        steps: Vec<String>,
    },
    Reset,
}

struct View {
    width: u32,
    height: u32,
    scale: f32,
    top: f32,
}

impl View {
    fn new(width: u32, height: u32) -> Self {
        let scale = (width as f32 / 960.0).min(height as f32 / HEIGHT);
        Self {
            width,
            height,
            scale,
            top: (height as f32 - HEIGHT * scale) / 2.0,
        }
    }

    fn point(&self, x: f32, y: f32) -> (f32, f32) {
        let dy = (y * self.height as f32 - self.top) / self.scale;
        if (0.0..1.0).contains(&x) && (0.0..HEIGHT).contains(&dy) {
            (x * self.width as f32 / self.scale, HEIGHT - dy)
        } else {
            (-1.0, -1.0)
        }
    }

    fn rect(&self, [x0, y0, x1, y1]: [f32; 4]) -> [f32; 4] {
        let x = |coord| coord * self.scale / self.width as f32;
        let y = |coord| (coord * self.scale + self.top) / self.height as f32;
        [x(x0), y(y0), x(x1), y(y1)]
    }
}

struct Button {
    at: (f32, f32),
    from_device: bool,
    kind: i32,
    down: bool,
}

struct State {
    vm: Vm,
    renderer: Renderer,
    view: View,
    accumulator: f32,
    mouse: (f32, f32),
    presented_pointer: (f32, f32),
    using_focus: bool,
    buttons: VecDeque<Button>,
    draw: TriangleList,
    output: Vec<u8>,
    save_label: String,
}

thread_local! {
    static ENGINE: RefCell<Option<State>> = const { RefCell::new(None) };
    static INPUT: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
    static TEX_SIZE: RefCell<[u32; 2]> = const { RefCell::new([0; 2]) };
}

fn state<T: Default>(f: impl FnOnce(&mut State) -> T) -> T {
    ENGINE.with(|engine| engine.borrow_mut().as_mut().map(f).unwrap_or_default())
}

#[no_mangle]
pub extern "C" fn web_boot() -> i32 {
    clr::assets::init("/game");
    let Some(dll) = clr::assets::read("Assembly-CSharp.dll") else {
        return 1;
    };
    let mut vm = Vm::from_bytes(dll);
    vm.eng.screen = (960, 600);
    vm.eng.save_dir = "/save".into();
    vm.eng.desktop_panels = true;
    vm.eng.desktop_layout.pc_resolutions = true;
    vm.eng.hover_always = true;
    unity_api::load_prefs(&mut vm);
    if let Err(error) = vm.load_scene("/game") {
        eprintln!("web scene: {error}");
        return 2;
    }
    vm.awake_all();
    vm.enable_visual_motion();
    vm.enable_browser_saves();
    if let Err(error) = vm.start_desktop_save_picker() {
        eprintln!("web save menu: {error}");
        return 3;
    }
    ENGINE.with(|engine| {
        *engine.borrow_mut() = Some(State {
            vm,
            renderer: Renderer::new(960, 540),
            view: View::new(960, 540),
            accumulator: 0.0,
            mouse: (-1.0, -1.0),
            presented_pointer: (-1.0, -1.0),
            using_focus: false,
            buttons: VecDeque::new(),
            draw: TriangleList::default(),
            output: Vec::new(),
            save_label: "Autosave in 30s".into(),
        });
    });
    0
}

#[no_mangle]
pub extern "C" fn web_input(size: usize) -> usize {
    INPUT.with(|buffer| {
        let mut buffer = buffer.borrow_mut();
        buffer.resize(size, 0);
        buffer.as_mut_ptr() as usize
    })
}

#[no_mangle]
pub extern "C" fn web_command() -> usize {
    INPUT.with(|input| {
        state(|s| {
            let results = command_results(&input.borrow(), |event| s.event(event));
            s.output = serde_json::to_vec(&results).unwrap();
            s.output.len()
        })
    })
}

fn command_results(input: &[u8], mut dispatch: impl FnMut(Event) -> Option<Value>) -> Vec<Value> {
    let Ok(events) = serde_json::from_slice::<Vec<&RawValue>>(input) else {
        return vec![json!({"error": "Invalid browser command batch."})];
    };
    events
        .into_iter()
        .enumerate()
        .filter_map(|(index, raw)| match serde_json::from_str(raw.get()) {
            Ok(event) => dispatch(event),
            Err(_) => Some(json!({"error": "Invalid browser event.", "event": index})),
        })
        .collect()
}

#[no_mangle]
pub extern "C" fn web_output() -> usize {
    state(|s| s.output.as_ptr() as usize)
}

#[no_mangle]
pub extern "C" fn web_frame(width: u32, height: u32, seconds: f32) -> usize {
    state(|s| {
        if width == 0 || height == 0 {
            return 0;
        }
        let resized = (width, height) != (s.view.width, s.view.height);
        s.view = View::new(width, height);
        s.vm.eng
            .desktop_layout
            .set_width(width as f32 / s.view.scale);
        s.renderer.resize(width as usize, height as usize, false);
        if resized {
            s.renderer.prepare_layout(&mut s.vm);
        }
        s.accumulator += seconds.clamp(0.0, 0.25);
        while s.accumulator >= STEP {
            s.tick();
            s.accumulator -= STEP;
        }
        let point = s.pointer();
        if s.buttons.is_empty() && (resized || s.presented_pointer != point) {
            s.presented_pointer = point;
            s.vm.eng.mouse = point;
            if (resized || s.vm.eng.ptr.last_pos != point)
                && (!s.vm.eng.mouse_down
                    || s.vm.pointer_holds_scroll_view()
                    || s.vm.dropdown_is_open())
            {
                s.vm.pointer_update();
            }
            s.vm.update_color_picker_pointer();
        }
        s.vm.browser_autosave_label(&s.save_label);
        s.renderer.set_subframe_time(s.accumulator);
        s.renderer
            .emit_frame(&mut s.vm, s.view.scale, 0.0, s.view.top);
        s.draw.draw_quads(&s.renderer.quads);
        let text = s.vm.eng.text_req.as_ref().and_then(|r| {
            focus::node_rect(&s.vm, &clr::unity::go_of(&r.comp))
                .map(|rect| json!({"value": r.text, "max": r.max, "numeric": r.numeric, "rect": s.view.rect(rect)}))
        });
        let file = s.vm.browser_file_request();
        let resolution =
            s.vm.eng
                .resolution_req
                .take()
                .and_then(|(width, height, fullscreen, at)| {
                    if at <= STARTUP_RESOLUTION_GUARD_FRAMES {
                        return None;
                    }
                    let (width, height) = clr::desktop_layout::pc_resolution(width, height);
                    Some((width, height, fullscreen))
                });
        let import =
            s.vm.browser_import_control()
                .map(|(slot, rect)| (slot, s.view.rect(rect)));
        let sounds = std::mem::take(&mut s.vm.eng.sound_queue);
        s.output = serde_json::to_vec(&json!({
            "active": s.vm.browser_active_slot(), "request": s.vm.browser_requested_slot(),
            "home": s.vm.at_desktop_start(), "text": text, "sounds": sounds, "file": file, "import": import,
            "resolution": resolution,
        })).unwrap();
        s.output.len()
    })
}

#[no_mangle]
pub extern "C" fn web_vertices() -> usize {
    state(|s| s.draw.vertices.as_ptr() as usize)
}
#[no_mangle]
pub extern "C" fn web_vertex_count() -> usize {
    state(|s| s.draw.vertices.len())
}
#[no_mangle]
pub extern "C" fn web_batches() -> usize {
    state(|s| s.draw.batches.as_ptr() as usize)
}
#[no_mangle]
pub extern "C" fn web_batch_count() -> usize {
    state(|s| s.draw.batches.len())
}
#[no_mangle]
pub extern "C" fn web_texture_size() -> usize {
    TEX_SIZE.with(|size| size.borrow().as_ptr() as usize)
}

#[no_mangle]
pub extern "C" fn web_texture(id: u32) -> usize {
    state(|s| {
        let (ptr, width, height) = if id == 1 {
            (s.renderer.atlas_pixels().as_ptr(), ATLAS_SIZE, ATLAS_SIZE)
        } else if let Some(tex) = s.renderer.tex_load(id) {
            (tex.px.as_ptr(), tex.w, tex.h)
        } else {
            return 0;
        };
        TEX_SIZE.with(|size| *size.borrow_mut() = [width as u32, height as u32]);
        ptr as usize
    })
}

#[no_mangle]
pub extern "C" fn web_texture_release(id: u32) {
    state(|s| s.renderer.tex_release(id));
}

#[no_mangle]
pub extern "C" fn web_atlas_dirty() -> u32 {
    state(|s| s.renderer.atlas_take_dirty() as u32)
}

impl State {
    fn pointer(&self) -> (f32, f32) {
        if self.using_focus {
            focus::pointer(&self.vm).unwrap_or((-1.0, -1.0))
        } else {
            self.view.point(self.mouse.0, self.mouse.1)
        }
    }

    fn tick(&mut self) {
        if let Some(button) = self.buttons.pop_front() {
            self.vm.eng.mouse = if button.from_device {
                self.view.point(button.at.0, button.at.1)
            } else {
                button.at
            };
            self.vm.eng.mouse_button = button.kind;
            self.vm.eng.mouse_down = button.down;
            self.vm.eng.mouse_down_edge = button.down;
            self.vm.eng.mouse_up_edge = !button.down;
        } else {
            self.vm.eng.mouse = self.pointer();
        }
        self.vm.frame(STEP);
    }

    fn event(&mut self, event: Event) -> Option<Value> {
        match event {
            Event::Move { x, y } => {
                if self.using_focus {
                    focus::clear(&mut self.vm);
                }
                self.using_focus = false;
                self.mouse = (x, y);
            }
            Event::Button { x, y, button, down } if (0..=2).contains(&button) => {
                if self.using_focus {
                    focus::clear(&mut self.vm);
                }
                self.using_focus = false;
                self.mouse = (x, y);
                let kind = [0, 2, 1][button as usize];
                if self.buttons.len() < 128 {
                    self.buttons.push_back(Button {
                        at: self.mouse,
                        from_device: true,
                        kind,
                        down,
                    });
                }
            }
            Event::Wheel { x, y, delta } => {
                self.vm.eng.mouse = self.view.point(x, y);
                self.vm.scroll_wheel(delta.clamp(-30.0, 30.0));
            }
            Event::Key {
                key,
                right,
                down,
                repeat,
            } => {
                let code = key_code(&key, right);
                let text_open = self.vm.eng.text_req.is_some();
                update_key(&mut self.vm.eng, code, down, repeat, text_open);
            }
            Event::Navigate { action } if self.vm.eng.text_req.is_none() => self.navigate(&action),
            Event::Scroll { x, y } => {
                self.using_focus = true;
                focus::scroll(&mut self.vm, x.clamp(-90.0, 90.0), y.clamp(-90.0, 90.0));
            }
            Event::Text { value } => {
                if let Some(request) = self.vm.eng.text_req.take() {
                    if let Some(value) = value {
                        self.vm.input_commit(
                            &request.comp,
                            &value.chars().take(1024).collect::<String>(),
                        );
                    }
                }
            }
            Event::Profile {
                online,
                locked,
                reload,
            } => self.vm.browser_profile_changed(online, locked, reload),
            Event::Lease { granted } => {
                return Some(json!({"error": self.vm.browser_finish_request(granted).err()}))
            }
            Event::SaveLabel { message } => self.save_label = message,
            Event::Notice { message } => self.vm.browser_save_notice(message),
            Event::Import { slot } => {
                return Some(
                    json!({"error": self.vm.import_browser_save("upload.txt", slot).err()}),
                )
            }
            Event::Export { slot } => {
                return Some(match self.vm.browser_export(slot) {
                    Ok(save) => json!({"save": save}),
                    Err(error) => json!({"error": error}),
                })
            }
            #[cfg(feature = "audit")]
            Event::Audit { steps } => {
                clr::script::run_steps(&mut self.vm, &steps);
                let menus: Vec<_> = clr::dev::menu_roots(&self.vm)
                    .iter()
                    .map(|node| clr::unity::with_go(node, |go| (go.name.clone(), go.port_hidden)))
                    .collect();
                let text = focus::visible_texts(&mut self.vm);
                let controls: Vec<_> = focus::collect(&mut self.vm)
                    .into_iter()
                    .map(|node| (node.label, node.rect, node.kind))
                    .collect();
                return Some(json!({"menus": menus, "text": text, "controls": controls}));
            }
            Event::Reset => {
                self.buttons.clear();
                self.using_focus = false;
                focus::clear(&mut self.vm);
                self.mouse = (-1.0, -1.0);
                self.vm.eng.keys_down.clear();
                self.vm.eng.keys_edge.clear();
                if self.vm.eng.mouse_down {
                    self.buttons.push_back(Button {
                        at: self.mouse,
                        from_device: true,
                        kind: self.vm.eng.mouse_button,
                        down: false,
                    });
                }
            }
            _ => {}
        }
        None
    }

    fn navigate(&mut self, action: &str) {
        self.using_focus = true;
        let direction = match action {
            "up" => 'u',
            "down" => 'd',
            "left" => 'l',
            "right" => 'r',
            "activate" => 'a',
            "back" => 'b',
            "prev" => 'p',
            "next" => 'n',
            _ => return,
        };
        let (click, _) = focus::pad_action(&mut self.vm, direction, false);
        if let Some(at) = click {
            self.buttons.push_back(Button {
                at,
                from_device: false,
                kind: 0,
                down: true,
            });
            self.buttons.push_back(Button {
                at,
                from_device: false,
                kind: 0,
                down: false,
            });
        }
    }
}

fn update_key(
    engine: &mut clr::unity::Engine,
    code: i32,
    down: bool,
    repeat: bool,
    text_open: bool,
) {
    if !down {
        engine.keys_down.remove(&code);
    } else if code != 0 && !text_open {
        engine.keys_down.insert(code);
        if !repeat {
            engine.keys_edge.insert(code);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn malformed_event_does_not_discard_adjacent_events() {
        let mut positions = Vec::new();
        let replies = command_results(
            br#"[{"type":"move","x":0.25,"y":0.5},{"type":"move"},{"type":"move","x":0.75,"y":0.5}]"#,
            |event| {
                if let Event::Move { x, y } = event {
                    positions.push((x, y));
                }
                None
            },
        );
        assert_eq!(positions, [(0.25, 0.5), (0.75, 0.5)]);
        assert_eq!(
            replies,
            [json!({"error": "Invalid browser event.", "event": 1})]
        );
    }

    #[test]
    fn malformed_batch_returns_an_error() {
        let replies = command_results(b"[", |_| panic!("unexpected event"));
        assert_eq!(
            replies,
            [json!({"error": "Invalid browser command batch."})]
        );
    }

    #[test]
    fn key_release_is_delivered_while_typing() {
        let mut engine = clr::unity::Engine::default();
        update_key(&mut engine, 275, true, false, false);
        assert!(engine.keys_down.contains(&275));
        update_key(&mut engine, 275, false, false, true);
        update_key(&mut engine, 276, true, false, true);
        assert!(engine.keys_down.is_empty());
        assert!(!engine.keys_edge.contains(&276));
    }
}

fn key_code(key: &str, right: bool) -> i32 {
    match key {
        "ArrowUp" => 273,
        "ArrowDown" => 274,
        "ArrowRight" => 275,
        "ArrowLeft" => 276,
        "Shift" => {
            if right {
                303
            } else {
                304
            }
        }
        "Control" => {
            if right {
                305
            } else {
                306
            }
        }
        "Alt" => {
            if right {
                307
            } else {
                308
            }
        }
        " " => 32,
        "PageUp" => 280,
        "PageDown" => 281,
        other => clr::input::key_by_name(other),
    }
}
