//! Browser WASI reactor. The engine runs entirely inside the browser.
#![cfg(any(target_arch = "wasm32", test))]

use clr::{
    platform_api::{Action, Engine, MouseButton, Pointer, TriangleList},
    session::{Session, SessionConfig},
    Vm,
};
use serde::Deserialize;
use serde_json::{json, value::RawValue, Value};
use std::cell::RefCell;

const WIDTH: f32 = 960.0;
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

/// Output size, to convert between the page's normalised coordinates and device pixels.
#[derive(Clone, Copy)]
struct View {
    width: u32,
    height: u32,
}

impl View {
    fn scale(&self) -> f32 {
        (self.width as f32 / WIDTH).min(self.height as f32 / HEIGHT)
    }

    fn top(&self) -> f32 {
        (self.height as f32 - HEIGHT * self.scale()) / 2.0
    }

    /// Game rectangle (y up from the top of the screen, game pixels) to page fractions.
    fn rect(&self, [x0, y0, x1, y1]: [f32; 4]) -> [f32; 4] {
        let (scale, top) = (self.scale(), self.top());
        let x = |coord| coord * scale / self.width as f32;
        let y = |coord| (coord * scale + top) / self.height as f32;
        [x(x0), y(y0), x(x1), y(y1)]
    }
}

struct State {
    session: Session,
    view: View,
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
    let mut config = SessionConfig::new("/game", "/save", 960, 540);
    config.before_home = Some(|vm: &mut Vm| vm.enable_hosted_saves());
    let session = match Session::open(config) {
        Ok(session) => session,
        Err(error) => {
            eprintln!("web boot: {error}");
            return 1;
        }
    };
    ENGINE.with(|engine| {
        *engine.borrow_mut() = Some(State {
            session,
            view: View {
                width: 960,
                height: 540,
            },
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
        s.view = View { width, height };
        s.session.resize(width, height);
        s.session.advance(seconds);
        s.draw.vertices.clear();
        s.draw.batches.clear();
        s.session.draw(&mut s.draw);
        let view = s.view;
        s.session.hosted_autosave_label(&s.save_label);
        let text = s.session.text_field().map(|t| {
            json!({"value": t.value, "max": t.max, "numeric": t.numeric, "rect": view.rect(t.rect)})
        });
        let file = s.session.hosted_file_request();
        let resolution = s
            .session
            .take_resolution_request_after(STARTUP_RESOLUTION_GUARD_FRAMES);
        let import = s
            .session
            .hosted_import_control()
            .map(|(slot, rect)| (slot, view.rect(rect)));
        let sounds = s.session.take_sounds();
        s.output = serde_json::to_vec(&json!({
            "active": s.session.hosted_active_slot(), "request": s.session.hosted_requested_slot(),
            "home": s.session.at_home(), "text": text, "sounds": sounds, "file": file, "import": import,
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
        let Some(texture) = s.session.texture(id) else {
            return 0;
        };
        TEX_SIZE.with(|size| *size.borrow_mut() = [texture.width, texture.height]);
        texture.rgba.as_ptr() as usize
    })
}

#[no_mangle]
pub extern "C" fn web_texture_release(id: u32) {
    state(|s| s.session.release_texture(id));
}

#[no_mangle]
pub extern "C" fn web_atlas_dirty() -> u32 {
    state(|s| s.session.atlas_changed() as u32)
}

impl State {
    fn event(&mut self, event: Event) -> Option<Value> {
        let (width, height) = (self.view.width as f32, self.view.height as f32);
        match event {
            Event::Move { x, y } => self.session.set_pointer(Pointer {
                x: x * width,
                y: y * height,
                down: false,
            }),
            Event::Button { x, y, button, down } => {
                let button = match button {
                    0 => MouseButton::Primary,
                    1 => MouseButton::Middle,
                    2 => MouseButton::Secondary,
                    _ => return None,
                };
                self.session.press(x * width, y * height, button, down);
            }
            Event::Wheel { x, y, delta } => self.session.wheel(x * width, y * height, delta),
            Event::Key {
                key,
                right,
                down,
                repeat,
            } => self.session.key(key_code(&key, right), down, repeat),
            Event::Navigate { action } => {
                let action = match action.as_str() {
                    "up" => Action::Up,
                    "down" => Action::Down,
                    "left" => Action::Left,
                    "right" => Action::Right,
                    "activate" => Action::Activate,
                    "back" => Action::Back,
                    "prev" => Action::Prev,
                    "next" => Action::Next,
                    _ => return None,
                };
                self.session.action(action);
            }
            Event::Scroll { x, y } => self.session.scroll(x, y),
            Event::Text { value } => self.session.submit_text(value.as_deref()),
            Event::Profile {
                online,
                locked,
                reload,
            } => self.session.hosted_profile_changed(online, locked, reload),
            Event::Lease { granted } => {
                return Some(json!({"error": self.session.hosted_finish_request(granted).err()}))
            }
            Event::SaveLabel { message } => self.save_label = message,
            Event::Notice { message } => self.session.hosted_save_notice(message),
            Event::Import { slot } => {
                return Some(
                    json!({"error": self.session.import_hosted_save("upload.txt", slot).err()}),
                )
            }
            Event::Export { slot } => {
                return Some(match self.session.hosted_export(slot) {
                    Ok(save) => json!({"save": save}),
                    Err(error) => json!({"error": error}),
                })
            }
            #[cfg(feature = "audit")]
            Event::Audit { steps } => {
                self.session.run_script(&steps);
                let seen = self.session.inspect();
                let controls: Vec<_> = seen
                    .controls
                    .into_iter()
                    .map(|node| (node.label, node.rect, node.kind))
                    .collect();
                return Some(json!({"menus": seen.menus, "text": seen.text, "controls": controls}));
            }
            Event::Reset => self.session.reset_input(),
        }
        None
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
