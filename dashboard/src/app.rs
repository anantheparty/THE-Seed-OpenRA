use makepad_widgets::*;
use crate::ws_client::{start_ws_client, ClientAction, DashboardMessage};
use crate::components::*;
use std::sync::mpsc::{channel, Receiver};

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;
    use crate::components::left_panel::LeftPanel;
    use crate::components::right_panel::RightPanel;
    use crate::components::tab_view::*;

    App = {{App}} {
        ui: <Window> {
            show_bg: true
            width: Fill,
            height: Fill

            draw_bg: { color: #1a1a26 }

            body = <View> {
                width: Fill,
                height: Fill,
                flow: Down,
                spacing: 0,

                // Top Bar
                <View> {
                    width: Fill,
                    height: 50,
                    padding: {left: 20, right: 20},
                    flow: Right,
                    align: {y: 0.5},
                    show_bg: true,
                    draw_bg: { color: #252530 }

                    <Label> {
                        text: "THE SEED - OpenRA Agent Dashboard",
                        draw_text: {
                            color: #00ff88,
                            text_style: { font_size: 16.0 }
                        }
                    }

                    <View> { width: Fill }

                    version_label = <Label> {
                        text: "v0.2.0",
                        draw_text: {
                            color: #666,
                            text_style: { font_size: 10.0 }
                        }
                    }
                }

                // Main Content Area (Three Panels)
                <View> {
                    width: Fill,
                    height: Fill,
                    flow: Right,
                    spacing: 0,

                    // Left Panel
                    left_panel = <LeftPanel> {}

                    // Center Panel (Tab View)
                    center_panel = <TabView> {}

                    // Right Panel
                    right_panel = <RightPanel> {}
                }

                // Bottom Bar
                <View> {
                    width: Fill,
                    height: 50,
                    padding: {left: 20, right: 20},
                    flow: Right,
                    spacing: 10,
                    align: {y: 0.5},
                    show_bg: true,
                    draw_bg: { color: #252530 }

                    <Label> {
                        text: "Command:",
                        draw_text: {
                            color: #888,
                            text_style: { font_size: 12.0 }
                        }
                    }

                    command_input = <TextInput> {
                        width: Fill,
                        height: 32,
                        text: ""
                    }

                    send_button = <Button> {
                        width: Fit,
                        height: 32,
                        padding: {left: 20, right: 20},
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
    #[rust] current_tab: usize,
}

impl LiveRegister for App {
    fn live_register(cx: &mut Cx) {
        makepad_widgets::live_design(cx);
        crate::components::left_panel::live_design(cx);
        crate::components::right_panel::live_design(cx);
        crate::components::tab_view::live_design(cx);
        crate::components::metrics_card::live_design(cx);
        crate::components::log_viewer::live_design(cx);
        crate::components::history_timeline::live_design(cx);
        crate::components::memory_view::live_design(cx);
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
    fn handle_startup(&mut self, cx: &mut Cx) {
        println!("App started, connecting to WS...");
        let (tx, rx) = channel();
        self.receiver = Some(rx);

        // Start WS Client
        start_ws_client("ws://127.0.0.1:8080".to_string(), tx, Box::new(|| {}));

        self.timer = cx.start_timeout(0.1);
        self.current_tab = 0;

        // Initialize tab state
        self.switch_tab(cx, 0);
    }

    fn handle_actions(&mut self, cx: &mut Cx, actions: &Actions) {
        // Handle send button
        if self.ui.button(id!(send_button)).clicked(&actions) {
            let cmd = self.ui.text_input(id!(command_input)).text();
            println!("Sending command: {}", cmd);
            // TODO: Send to WS
            self.ui.text_input(id!(command_input)).set_text(cx, "");
            self.ui.redraw(cx);
        }

        // Handle tab switching
        if self.ui.button(id!(agent_tab)).clicked(&actions) {
            self.switch_tab(cx, 0);
        }
        if self.ui.button(id!(trace_tab)).clicked(&actions) {
            self.switch_tab(cx, 1);
        }
        if self.ui.button(id!(memory_tab)).clicked(&actions) {
            self.switch_tab(cx, 2);
        }
        if self.ui.button(id!(game_tab)).clicked(&actions) {
            self.switch_tab(cx, 3);
        }
    }
}

impl App {
    fn switch_tab(&mut self, cx: &mut Cx, tab_index: usize) {
        self.current_tab = tab_index;

        // Hide all content views
        self.ui.view(id!(agent_content)).set_visible(cx, false);
        self.ui.view(id!(trace_content)).set_visible(cx, false);
        self.ui.view(id!(memory_content)).set_visible(cx, false);
        self.ui.view(id!(game_content)).set_visible(cx, false);

        // Show selected content view
        match tab_index {
            0 => {
                self.ui.view(id!(agent_content)).set_visible(cx, true);
            }
            1 => {
                self.ui.view(id!(trace_content)).set_visible(cx, true);
            }
            2 => {
                self.ui.view(id!(memory_content)).set_visible(cx, true);
            }
            3 => {
                self.ui.view(id!(game_content)).set_visible(cx, true);
            }
            _ => {}
        }

        self.ui.redraw(cx);
    }

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
                    self.ui.label(id!(status)).set_text(cx, "Connected");
                    self.ui.redraw(cx);
                }
                ClientAction::Disconnected => {
                    self.connected = false;
                    self.ui.label(id!(status)).set_text(cx, "Disconnected");
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
                // Update left panel
                self.ui.label(id!(current_state)).set_text(cx, &payload.fsm_state);
                self.ui.label(id!(goal)).set_text(cx, &payload.current_goal);

                let step_text = format!("{} / {}", payload.step_index, payload.plan_length);
                self.ui.label(id!(step_info)).set_text(cx, &step_text);

                // Update game state in game tab
                self.ui.label(id!(game_state_display)).set_text(cx, &payload.blackboard.game_basic_state);

                self.ui.redraw(cx);
            }
            DashboardMessage::Log(payload) => {
                println!("[{}] {}", payload.level, payload.message);
            }
            DashboardMessage::AgentMetrics(payload) => {
                // Update Agent Benchmark metrics
                self.ui.label(id!(tokens_card.value)).set_text(cx, &format!("{:.1}", payload.tokens_per_min));
                self.ui.label(id!(llm_calls_card.value)).set_text(cx, &format!("{:.2}", payload.llm_calls_per_min));
                self.ui.label(id!(tasks_card.value)).set_text(cx, &format!("{}", payload.active_tasks));
                self.ui.label(id!(actions_card.value)).set_text(cx, &format!("{}", payload.total_actions));
                self.ui.label(id!(volume_card.value)).set_text(cx, &format!("{}", payload.execution_volume));
                self.ui.label(id!(failure_card.value)).set_text(cx, &format!("{:.1}", payload.failure_rate * 100.0));
                self.ui.label(id!(recovery_card.value)).set_text(cx, &format!("{:.1}", payload.recovery_rate * 100.0));

                self.ui.redraw(cx);
            }
            DashboardMessage::GameMetrics(_payload) => {
                // TODO: Handle game metrics in phase 5
            }
            DashboardMessage::TraceEvent(payload) => {
                // Handle trace events
                match payload.event_type.as_str() {
                    "fsm_transition" => {
                        // TODO: Add to FSM history timeline
                        // For now, just log
                        println!("FSM Transition: {:?} -> {:?}", payload.from_state, payload.to_state);
                    }
                    "action_start" | "action_end" => {
                        // Update action detail view
                        if let Some(action_name) = &payload.action_name {
                            self.ui.label(id!(action_name)).set_text(cx, action_name);
                        }
                        let status = if payload.event_type == "action_start" { "Running" } else { "Completed" };
                        self.ui.label(id!(action_status)).set_text(cx, status);

                        let details = serde_json::to_string_pretty(&payload.details).unwrap_or_default();
                        self.ui.label(id!(action_details)).set_text(cx, &details);
                    }
                    "log" => {
                        // TODO: Add to log viewer
                        // For now, just print
                        println!("[TRACE LOG] {}", payload.details);
                    }
                    _ => {}
                }

                self.ui.redraw(cx);
            }
            DashboardMessage::MemoryUpdate(payload) => {
                // Update memory statistics
                self.ui.label(id!(total_entries)).set_text(cx, &format!("{}", payload.total_entries));
                self.ui.label(id!(recent_queries)).set_text(cx, &format!("{}", payload.recent_queries.len()));
                self.ui.label(id!(new_additions)).set_text(cx, &format!("{}", payload.recent_additions.len()));

                // TODO: Update entry list dynamically
                // For now just update stats

                self.ui.redraw(cx);
            }
        }
    }
}
