use makepad_widgets::*;
use crate::ws_client::{start_ws_client, ClientAction, DashboardMessage};
use std::sync::mpsc::{channel, Receiver};

live_design!{
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    App = {{App}} {
        ui: <Window> {
            show_bg: true
            width: Fill,
            height: Fill
            
            draw_bg: { color: #222 }
            
            body = <View> {
                flow: Down,
                padding: 10.0,
                spacing: 10.0,
                
                // Header
                <View> {
                    width: Fill, height: 50,
                    align: {x: 0.0, y: 0.5},
                    <Label> {
                        text: "THE SEED - OpenRA Agent Dashboard",
                        draw_text: {
                            color: #fff
                        }
                    }
                    <View> {width: Fill}
                    status_label = <Label> {
                        text: "Disconnected",
                        draw_text: {
                            color: #f00,
                            text_style: {font_size: 12.0}
                        }
                    }
                }
                
                // Main Content
                <View> {
                    width: Fill, height: Fill,
                    flow: Right,
                    spacing: 10.0,
                    
                    // Left: FSM State
                    <View> {
                        width: 200, height: Fill,
                        flow: Down,
                        spacing: 5.0,
                        show_bg: true,
                        draw_bg: { color: #333 }
                        padding: 10.0,
                        
                        <Label> { text: "FSM STATE", draw_text: { color: #aaa } }
                        
                        current_state_label = <Label> {
                            text: "-",
                            draw_text: { color: #0f0 }
                        }
                        
                        <View> {height: 20}
                        
                        <Label> { text: "GOAL", draw_text: { color: #aaa } }
                        goal_label = <Label> {
                            width: Fill,
                            text: "-",
                            draw_text: { color: #fff, wrap: Word }
                        }
                    }
                    
                    // Center: Blackboard Data
                    <View> {
                        width: Fill, height: Fill,
                        flow: Down,
                        spacing: 10.0,
                        show_bg: true,
                        draw_bg: { color: #2a2a2a }
                        padding: 10.0,
                        
                        <Label> { text: "BLACKBOARD", draw_text: { color: #aaa } }
                        
                        <Label> { text: "Game State:", draw_text: { color: #888 } }
                        game_state_label = <Label> {
                            width: Fill,
                            text: "Waiting for data...",
                            draw_text: { color: #ddd, wrap: Word }
                        }
                        
                        <View> {height: 10}
                        
                        <Label> { text: "Current Plan Step:", draw_text: { color: #888 } }
                        plan_step_label = <Label> {
                            width: Fill,
                            text: "-",
                            draw_text: { color: #ea0, wrap: Word }
                        }
                        
                        <View> {height: 10}
                         <Label> { text: "Action Result:", draw_text: { color: #888 } }
                        action_result_label = <Label> {
                            width: Fill,
                            text: "-",
                            draw_text: { color: #aaa, wrap: Word }
                        }
                    }
                }
                
                // Bottom: Command Input
                <View> {
                    width: Fill, height: 40,
                    flow: Right,
                    spacing: 10.0,
                    align: {y: 0.5},
                    
                    <Label> { text: "User Command:", draw_text: { color: #fff } }
                    command_input = <TextInput> {
                        width: Fill, height: 30,
                        text: "enter command..."
                    }
                    send_button = <Button> {
                        text: "Send"
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
    
    #[rust] receiver: Option<Receiver<ClientAction>>,
    #[rust] timer: Timer,
    #[rust] connected: bool,
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
        
        if let Event::Timer(e) = event {
            if e.timer_id == self.timer.0 {
                self.check_messages(cx);
                self.timer = cx.start_timeout(0.1);
            }
        }
    }
}

impl MatchEvent for App {
    fn handle_startup(&mut self, cx: &mut Cx){
        println!("App started, connecting to WS...");
        let (tx, rx) = channel();
        self.receiver = Some(rx);
        
        // Start WS Client
        start_ws_client("ws://127.0.0.1:8080".to_string(), tx, Box::new(|| {}));
        
        self.timer = cx.start_timeout(0.1);
    }
    
    fn handle_actions(&mut self, cx: &mut Cx, actions: &Actions) {
        if self.ui.button(id!(send_button)).clicked(&actions) {
            let cmd = self.ui.text_input(id!(command_input)).text();
            println!("Sending command: {}", cmd);
            // TODO: Send to WS
            self.ui.text_input(id!(command_input)).set_text(cx, "");
            self.ui.redraw(cx);
        }
    }
}

impl App {
    fn check_messages(&mut self, cx: &mut Cx) {
        let Some(rx) = self.receiver.as_ref() else { return };

        let mut actions = Vec::new();
        while let Ok(action) = rx.try_recv() {
            actions.push(action);
        }

        for action in actions {
            match action {
                ClientAction::Connected => {
                    self.connected = true;
                    self.ui.label(id!(status_label)).set_text(cx, "Connected");
                    self.ui.redraw(cx);
                }
                ClientAction::Disconnected => {
                    self.connected = false;
                    self.ui.label(id!(status_label)).set_text(cx, "Disconnected");
                    self.ui.redraw(cx);
                }
                ClientAction::Error(e) => {
                    println!("WS Error: {}", e);
                }
                ClientAction::Message(msg) => {
                    self.handle_dashboard_message(cx, msg);
                }
            }
        }
    }
    
    fn handle_dashboard_message(&mut self, cx: &mut Cx, msg: DashboardMessage) {
        match msg {
            DashboardMessage::Init(payload) | DashboardMessage::Update(payload) => {
                self.ui.label(id!(current_state_label)).set_text(cx, &payload.fsm_state);
                self.ui.label(id!(goal_label)).set_text(cx, &payload.current_goal);
                self.ui.label(id!(game_state_label)).set_text(cx, &payload.blackboard.game_basic_state);
                
                let step_str = serde_json::to_string_pretty(&payload.blackboard.current_step).unwrap_or_default();
                self.ui.label(id!(plan_step_label)).set_text(cx, &step_str);

                let result_str = serde_json::to_string_pretty(&payload.blackboard.action_result).unwrap_or_default();
                self.ui.label(id!(action_result_label)).set_text(cx, &result_str);
                
                self.ui.redraw(cx);
            }
            DashboardMessage::Log(payload) => {
                println!("[{}] {}", payload.level, payload.message);
            }
        }
    }
}
