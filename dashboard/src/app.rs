use makepad_widgets::*;
use crate::ws_client::{start_ws_client, ClientAction, DashboardMessage, WsClientHandle};
use std::sync::mpsc::{channel, Receiver};

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    // Import component modules
    use crate::components::left_panel::*;
    use crate::components::right_panel::*;
    use crate::components::tab_view::*;

    // Status Dot component
    StatusDot = <View> {
        width: 10,
        height: 10,
        show_bg: true,
        draw_bg: {
            color: #f38ba8
            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                sdf.circle(5.0, 5.0, 4.0);
                sdf.fill(self.color);
                return sdf.result;
            }
        }
    }

    App = {{App}} {
        ui: <Window> {
            show_bg: true
            width: Fill,
            height: Fill
            draw_bg: { color: #11111b }

            body = <View> {
                width: Fill,
                height: Fill,
                flow: Down,

                // Top Bar
                <View> {
                    width: Fill,
                    height: 64,
                    padding: {left: 24, right: 24},
                    flow: Right,
                    align: {y: 0.5},
                    show_bg: true,
                    draw_bg: { color: #1e1e2e }

                    <View> {
                        flow: Right,
                        align: {y: 0.5},
                        spacing: 12,

                        <Label> {
                            text: "THE SEED",
                            draw_text: {
                                color: #89b4fa,
                                text_style: { font_size: 22.0 }
                            }
                        }

                        <Label> {
                            text: "OpenRA Agent Dashboard",
                            draw_text: {
                                color: #6c7086,
                                text_style: { font_size: 15.0 }
                            }
                        }
                    }

                    <View> { width: Fill }

                    <View> {
                        flow: Right,
                        align: {y: 0.5},
                        spacing: 12,

                        <View> {
                            flow: Right,
                            align: {y: 0.5},
                            spacing: 6,

                            conn_dot = <StatusDot> {}

                            conn_status = <Label> {
                                text: "Disconnected",
                                draw_text: {
                                    color: #f38ba8,
                                    text_style: { font_size: 11.0 }
                                }
                            }
                        }

                        version_label = <Label> {
                            text: "v0.3.0",
                            draw_text: {
                                color: #6c7086,
                                text_style: { font_size: 11.0 }
                            }
                        }
                    }
                }

                // Main Content - 3 panel layout
                <View> {
                    width: Fill,
                    height: Fill,
                    flow: Right,
                    spacing: 0,

                    left_panel = <LeftPanel> {}

                    <View> { width: 1, height: Fill, show_bg: true, draw_bg: { color: #313244 } }

                    center_panel = <TabView> {}

                    <View> { width: 1, height: Fill, show_bg: true, draw_bg: { color: #313244 } }

                    right_panel = <RightPanel> {}
                }

                // Bottom Bar
                <View> {
                    width: Fill,
                    height: 68,
                    padding: {left: 24, right: 24},
                    flow: Right,
                    spacing: 12,
                    align: {y: 0.5},
                    show_bg: true,
                    draw_bg: { color: #1e1e2e }

                    <Label> {
                        text: "Command:",
                        draw_text: {
                            color: #6c7086,
                            text_style: { font_size: 14.0 }
                        }
                    }

                    command_input = <TextInput> {
                        width: Fill,
                        height: 42,
                        text: ""
                    }

                    send_button = <Button> {
                        text: "Send",
                        padding: {left: 28, right: 28, top: 12, bottom: 12}
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
    #[rust] ws_handle: Option<WsClientHandle>,
    #[rust] timer: Timer,
    #[rust] connected: bool,
    #[rust] current_tab: usize,
    #[rust] trace_events: Vec<crate::ws_client::TraceEventPayload>,
    #[rust] memory_data: Option<crate::ws_client::MemoryPayload>,
    #[rust] game_metrics: Option<crate::ws_client::GameMetricsPayload>,
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
    fn handle_startup(&mut self, cx: &mut Cx) {
        println!("App started, connecting to WS...");
        let (tx, rx) = channel();
        self.receiver = Some(rx);

        let handle = start_ws_client("ws://127.0.0.1:8080".to_string(), tx, Box::new(|| {}));
        self.ws_handle = Some(handle);

        self.timer = cx.start_timeout(0.1);
        self.current_tab = 0;
        self.switch_tab(cx, 0);
    }

    fn handle_actions(&mut self, cx: &mut Cx, actions: &Actions) {
        if self.ui.button(id!(send_button)).clicked(&actions) {
            let cmd = self.ui.text_input(id!(command_input)).text();
            println!("Sending command: {}", cmd);

            if let Some(handle) = &self.ws_handle {
                handle.send_command(cmd);
            }

            self.ui.text_input(id!(command_input)).set_text(cx, "");
            self.ui.redraw(cx);
        }

        // Tab button clicks - using the path to TabView components
        if self.ui.button(id!(center_panel.agent_tab)).clicked(&actions) {
            self.switch_tab(cx, 0);
        }
        if self.ui.button(id!(center_panel.trace_tab)).clicked(&actions) {
            self.switch_tab(cx, 1);
        }
        if self.ui.button(id!(center_panel.memory_tab)).clicked(&actions) {
            self.switch_tab(cx, 2);
        }
        if self.ui.button(id!(center_panel.game_tab)).clicked(&actions) {
            self.switch_tab(cx, 3);
        }
    }
}

impl App {
    fn switch_tab(&mut self, cx: &mut Cx, tab_index: usize) {
        self.current_tab = tab_index;

        // Hide all content views - using the path to TabView components
        self.ui.view(id!(center_panel.agent_content)).set_visible(cx, false);
        self.ui.view(id!(center_panel.trace_content)).set_visible(cx, false);
        self.ui.view(id!(center_panel.memory_content)).set_visible(cx, false);
        self.ui.view(id!(center_panel.game_content)).set_visible(cx, false);

        // Show the selected tab's content
        match tab_index {
            0 => self.ui.view(id!(center_panel.agent_content)).set_visible(cx, true),
            1 => self.ui.view(id!(center_panel.trace_content)).set_visible(cx, true),
            2 => self.ui.view(id!(center_panel.memory_content)).set_visible(cx, true),
            3 => self.ui.view(id!(center_panel.game_content)).set_visible(cx, true),
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
                    // Update top bar connection status
                    self.ui.label(id!(conn_status)).set_text(cx, "Connected");
                    self.ui.view(id!(conn_dot)).apply_over(cx, live!{
                        draw_bg: { color: #a6e3a1 }
                    });
                    // Update right panel connection status
                    self.ui.label(id!(right_panel.status)).set_text(cx, "Connected");
                    self.ui.redraw(cx);
                }
                ClientAction::Disconnected => {
                    self.connected = false;
                    // Update top bar connection status
                    self.ui.label(id!(conn_status)).set_text(cx, "Disconnected");
                    self.ui.view(id!(conn_dot)).apply_over(cx, live!{
                        draw_bg: { color: #f38ba8 }
                    });
                    // Update right panel connection status
                    self.ui.label(id!(right_panel.status)).set_text(cx, "Disconnected");
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
                // Update left panel components
                self.ui.label(id!(left_panel.current_state)).set_text(cx, &payload.fsm_state);
                self.ui.label(id!(left_panel.goal)).set_text(cx, &payload.current_goal);

                let step_text = format!("{} / {}", payload.step_index, payload.plan_length);
                self.ui.label(id!(left_panel.step_info)).set_text(cx, &step_text);

                // Update plan display
                self.update_plan_display(cx, &payload);

                self.ui.redraw(cx);
            }
            DashboardMessage::Log(payload) => {
                println!("[{}] {}", payload.level, payload.message);
            }
            DashboardMessage::AgentMetrics(payload) => {
                // Update agent metrics in the Agent Benchmark tab
                self.ui.label(id!(center_panel.agent_content.tokens_card.value))
                    .set_text(cx, &format!("{:.0}", payload.tokens_per_min));
                self.ui.label(id!(center_panel.agent_content.llm_calls_card.value))
                    .set_text(cx, &format!("{}", payload.llm_calls_per_min as i32));
                self.ui.label(id!(center_panel.agent_content.tasks_card.value))
                    .set_text(cx, &format!("{}", payload.active_tasks));
                self.ui.label(id!(center_panel.agent_content.actions_card.value))
                    .set_text(cx, &format!("{}", payload.total_actions));
                self.ui.label(id!(center_panel.agent_content.volume_card.value))
                    .set_text(cx, &format!("{}", payload.execution_volume));
                self.ui.label(id!(center_panel.agent_content.failure_card.value))
                    .set_text(cx, &format!("{:.1}", payload.failure_rate));
                self.ui.label(id!(center_panel.agent_content.recovery_card.value))
                    .set_text(cx, &format!("{:.1}", payload.recovery_rate));
                self.ui.redraw(cx);
            }
            DashboardMessage::TraceEvent(payload) => {
                // Keep last 50 trace events
                self.trace_events.push(payload);
                if self.trace_events.len() > 50 {
                    self.trace_events.remove(0);
                }
                self.update_trace_view(cx);
            }
            DashboardMessage::MemoryUpdate(payload) => {
                self.memory_data = Some(payload);
                self.update_memory_view(cx);
            }
            DashboardMessage::GameMetrics(payload) => {
                self.game_metrics = Some(payload);
                self.update_game_view(cx);
            }
        }
    }

    fn update_plan_display(&mut self, cx: &mut Cx, payload: &crate::ws_client::FsmStatePayload) {
        let plan = &payload.blackboard.plan;
        let current_step = payload.step_index;

        if plan.is_empty() {
            self.ui.label(id!(left_panel.plan_list)).set_text(cx, "No plan yet");
            return;
        }

        let mut plan_text = String::new();
        for (idx, step) in plan.iter().enumerate() {
            let step_num = idx + 1;
            let step_str = step.get("step")
                .and_then(|v| v.as_str())
                .unwrap_or("?");

            if step_num == current_step as usize {
                // Current step - highlight with arrow
                plan_text.push_str(&format!("▶ {}. {}\n", step_num, step_str));
            } else if step_num < current_step as usize {
                // Completed step - checkmark
                plan_text.push_str(&format!("✓ {}. {}\n", step_num, step_str));
            } else {
                // Future step - plain
                plan_text.push_str(&format!("  {}. {}\n", step_num, step_str));
            }
        }

        self.ui.label(id!(left_panel.plan_list)).set_text(cx, &plan_text);
    }

    fn update_trace_view(&mut self, cx: &mut Cx) {
        // The trace view is now handled by the TraceTab component
        // For now, we can update it later when we implement dynamic log viewer
        self.ui.redraw(cx);
    }

    fn update_memory_view(&mut self, cx: &mut Cx) {
        if let Some(ref mem) = self.memory_data {
            // Update memory stats card
            self.ui.label(id!(center_panel.memory_content.stats_card.total_entries))
                .set_text(cx, &format!("{}", mem.total_entries));
            self.ui.label(id!(center_panel.memory_content.stats_card.recent_queries))
                .set_text(cx, &format!("{}", mem.recent_queries.len()));
            self.ui.label(id!(center_panel.memory_content.stats_card.new_additions))
                .set_text(cx, &format!("{}", mem.recent_additions.len()));
            self.ui.redraw(cx);
        }
    }

    fn update_game_view(&mut self, cx: &mut Cx) {
        if let Some(ref metrics) = self.game_metrics {
            // Update game metrics cards
            self.ui.label(id!(center_panel.game_content.fps_card.value))
                .set_text(cx, &format!("{:.1}", metrics.fps));
            self.ui.label(id!(center_panel.game_content.frame_time_card.value))
                .set_text(cx, &format!("{:.2}", metrics.frame_time_ms));
            self.ui.label(id!(center_panel.game_content.tick_rate_card.value))
                .set_text(cx, &format!("{:.1}", metrics.tick_rate));
            self.ui.label(id!(center_panel.game_content.entity_count_card.value))
                .set_text(cx, &format!("{}", metrics.entity_count));
            self.ui.redraw(cx);
        }
    }
}

