use makepad_widgets::*;

live_design!{
    import makepad_widgets::base::*;
    import makepad_widgets::theme_desktop_dark::*;

    App = {{App}} {
        ui: <Window> {
            show_bg: true
            width: Fill,
            height: Fill
            
            draw_bg: {
                fn pixel(self) -> vec4 {
                    return mix(#333, #111, self.geom_pos.y)
                }
            }
            
            body = <View> {
                flow: Down,
                align: {x: 0.5, y: 0.5},
                spacing: 20.0,
                
                <Label> {
                    text: "OpenRA Agent Dashboard",
                    draw_text: {
                        color: #ffffff
                        text_style: {font_size: 32.0}
                    }
                }
                
                <Label> {
                    text: "Waiting for connection...",
                    draw_text: {
                        color: #aaaaaa
                        text_style: {font_size: 16.0}
                    }
                }
            }
        }
    }
}

app_main!(App);

#[derive(Live, LiveHook)]
pub struct App {
    #[live] ui: WidgetRef,
}

impl LiveRegister for App {
    fn live_register(cx: &mut Cx) {
        makepad_widgets::live_design(cx);
    }
}

impl AppMain for App {
    fn handle_event(&mut self, cx: &mut Cx, event: &Event) {
        self.match_event(cx, event);
        self.ui.handle_event(cx, event, &mut Scope::empty());
    }
}

impl MatchEvent for App {
    fn handle_startup(&mut self, _cx: &mut Cx){
        println!("App started");
    }
}
