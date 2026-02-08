use makepad_widgets::*;
use crate::ws_client::{start_ws_client, ClientAction, DashboardMessage, WsClientHandle};
use std::sync::mpsc::{channel, Receiver};

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;

    // Modern Card component
    Card = <View> {
        width: Fill,
        height: Fit,
        margin: {bottom: 16}
        padding: 20,
        show_bg: true,
        draw_bg: {
            color: #1e1e2e
            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                sdf.box(1.0, 1.0, self.rect_size.x - 2.0, self.rect_size.y - 2.0, 12.0);
                sdf.fill(self.color);
                return sdf.result;
            }
        }
    }

    // Status Dot
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

    // Left Panel
    LeftPanel = <View> {
        width: 300,
        height: Fill,
        show_bg: true,
        draw_bg: { color: #11111b }
        flow: Down,
        padding: 20,

        <Label> {
            text: "FSM STATE",
            margin: {bottom: 20}
            draw_text: {
                color: #6c7086,
                text_style: { font_size: 11.0 }
            }
        }

        <Card> {
            flow: Down,
            spacing: 10,

            <Label> {
                text: "Current State",
                draw_text: {
                    color: #89b4fa,
                    text_style: { font_size: 13.0 }
                }
            }

            current_state = <Label> {
                text: "IDLE",
                draw_text: {
                    color: #cdd6f4,
                    text_style: { font_size: 24.0 }
                }
            }
        }

        <Card> {
            flow: Down,
            spacing: 10,

            <Label> {
                text: "Current Goal",
                draw_text: {
                    color: #89b4fa,
                    text_style: { font_size: 13.0 }
                }
            }

            goal = <Label> {
                text: "Waiting for command...",
                draw_text: {
                    color: #cdd6f4,
                    text_style: { font_size: 14.0 },
                    wrap: Word
                }
            }
        }

        <Card> {
            flow: Down,
            spacing: 10,

            <Label> {
                text: "Progress",
                draw_text: {
                    color: #89b4fa,
                    text_style: { font_size: 13.0 }
                }
            }

            step_info = <Label> {
                text: "0 / 0",
                draw_text: {
                    color: #f9e2af,
                    text_style: { font_size: 18.0 }
                }
            }
        }

        <Card> {
            flow: Down,
            spacing: 8,
            height: Fill,

            <Label> {
                text: "Plan",
                draw_text: {
                    color: #89b4fa,
                    text_style: { font_size: 13.0 }
                }
            }

            plan_list = <Label> {
                text: "No plan yet",
                draw_text: {
                    color: #cdd6f4,
                    text_style: { font_size: 12.0, line_spacing: 1.6 },
                    wrap: Word
                }
            }
        }
    }


    // Tab View
    TabView = <View> {
        width: Fill,
        height: Fill,
        flow: Down,
        show_bg: true,
        draw_bg: { color: #181825 }

        <View> {
            width: Fill,
            height: 56,
            flow: Right,
            spacing: 8,
            padding: {left: 20, right: 20, top: 12},
            show_bg: true,
            draw_bg: { color: #11111b }

            agent_tab = <Button> {
                text: "Agent Benchmark",
                padding: {left: 20, right: 20, top: 12, bottom: 12}
            }

            trace_tab = <Button> {
                text: "Trace",
                padding: {left: 20, right: 20, top: 12, bottom: 12}
            }

            memory_tab = <Button> {
                text: "Memory",
                padding: {left: 20, right: 20, top: 12, bottom: 12}
            }

            game_tab = <Button> {
                text: "Game State",
                padding: {left: 20, right: 20, top: 12, bottom: 12}
            }
        }

        <View> {
            width: Fill,
            height: Fill,
            padding: 24,

            agent_content = <View> {
                width: Fill,
                height: Fill,
                visible: true,
                flow: Down,
                spacing: 20,

                <Label> {
                    text: "Agent Benchmark",
                    draw_text: {
                        color: #cdd6f4,
                        text_style: { font_size: 26.0 }
                    }
                }

                <Label> {
                    text: "Real-time agent performance metrics will appear here",
                    draw_text: {
                        color: #6c7086,
                        text_style: { font_size: 15.0 }
                    }
                }

                <View> {
                    width: Fill,
                    height: Fit,
                    flow: Right,
                    spacing: 16,
                    margin: {top: 20}

                    tokens_card = <Card> {
                        width: Fill,
                        flow: Down,
                        spacing: 8,

                        <Label> {
                            text: "Total Tokens",
                            draw_text: {
                                color: #6c7086,
                                text_style: { font_size: 12.0 }
                            }
                        }

                        value = <Label> {
                            text: "0",
                            draw_text: {
                                color: #cdd6f4,
                                text_style: { font_size: 28.0 }
                            }
                        }
                    }

                    llm_calls_card = <Card> {
                        width: Fill,
                        flow: Down,
                        spacing: 8,

                        <Label> {
                            text: "Total LLM Calls",
                            draw_text: {
                                color: #6c7086,
                                text_style: { font_size: 12.0 }
                            }
                        }

                        value = <Label> {
                            text: "0",
                            draw_text: {
                                color: #cdd6f4,
                                text_style: { font_size: 28.0 }
                            }
                        }
                    }
                }
            }

            trace_content = <View> {
                width: Fill,
                height: Fill,
                visible: false,
                flow: Down,
                spacing: 20,

                <Label> {
                    text: "Trace",
                    draw_text: {
                        color: #cdd6f4,
                        text_style: { font_size: 26.0 }
                    }
                }

                <Label> {
                    text: "FSM transitions and action execution traces",
                    draw_text: {
                        color: #6c7086,
                        text_style: { font_size: 15.0 }
                    }
                }

                <Card> {
                    flow: Down,
                    height: Fill,

                    trace_log = <Label> {
                        text: "No trace events yet",
                        draw_text: {
                            color: #cdd6f4,
                            text_style: { font_size: 13.0, line_spacing: 1.4 },
                            wrap: Word
                        }
                    }
                }
            }

            memory_content = <View> {
                width: Fill,
                height: Fill,
                visible: false,
                flow: Down,
                spacing: 20,

                <Label> {
                    text: "Memory",
                    draw_text: {
                        color: #cdd6f4,
                        text_style: { font_size: 26.0 }
                    }
                }

                <Label> {
                    text: "Agent memory system statistics",
                    draw_text: {
                        color: #6c7086,
                        text_style: { font_size: 15.0 }
                    }
                }

                <Card> {
                    flow: Down,
                    height: Fit,

                    memory_info = <Label> {
                        text: "No memory data yet",
                        draw_text: {
                            color: #cdd6f4,
                            text_style: { font_size: 14.0, line_spacing: 1.6 },
                            wrap: Word
                        }
                    }
                }
            }

            game_content = <View> {
                width: Fill,
                height: Fill,
                visible: false,
                flow: Down,
                spacing: 20,

                <Label> {
                    text: "Game State",
                    draw_text: {
                        color: #cdd6f4,
                        text_style: { font_size: 26.0 }
                    }
                }

                <Label> {
                    text: "OpenRA game performance metrics",
                    draw_text: {
                        color: #6c7086,
                        text_style: { font_size: 15.0 }
                    }
                }

                <Card> {
                    flow: Down,
                    height: Fit,

                    game_info = <Label> {
                        text: "No game metrics yet",
                        draw_text: {
                            color: #cdd6f4,
                            text_style: { font_size: 16.0, line_spacing: 1.8 },
                            wrap: Word
                        }
                    }
                }
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
                            text: "v0.2.0",
                            draw_text: {
                                color: #6c7086,
                                text_style: { font_size: 11.0 }
                            }
                        }
                    }
                }

                // Main Content
                <View> {
                    width: Fill,
                    height: Fill,
                    flow: Right,
                    spacing: 1,

                    left_panel = <LeftPanel> {}
                    <View> { width: 1, height: Fill, show_bg: true, draw_bg: { color: #313244 } }
                    center_panel = <TabView> {}
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

        // Show the selected tab's content
        match tab_index {
            0 => self.ui.view(id!(agent_content)).set_visible(cx, true),
            1 => self.ui.view(id!(trace_content)).set_visible(cx, true),
            2 => self.ui.view(id!(memory_content)).set_visible(cx, true),
            3 => self.ui.view(id!(game_content)).set_visible(cx, true),
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
                    self.ui.label(id!(conn_status)).set_text(cx, "Connected");
                    // Update connection dot color to green
                    self.ui.view(id!(conn_dot)).apply_over(cx, live!{
                        draw_bg: { color: #a6e3a1 }
                    });
                    self.ui.redraw(cx);
                }
                ClientAction::Disconnected => {
                    self.connected = false;
                    self.ui.label(id!(conn_status)).set_text(cx, "Disconnected");
                    // Update connection dot color to red
                    self.ui.view(id!(conn_dot)).apply_over(cx, live!{
                        draw_bg: { color: #f38ba8 }
                    });
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
                self.ui.label(id!(current_state)).set_text(cx, &payload.fsm_state);
                self.ui.label(id!(goal)).set_text(cx, &payload.current_goal);

                let step_text = format!("{} / {}", payload.step_index, payload.plan_length);
                self.ui.label(id!(step_info)).set_text(cx, &step_text);

                // Update plan display
                self.update_plan_display(cx, &payload);

                self.ui.redraw(cx);
            }
            DashboardMessage::Log(payload) => {
                println!("[{}] {}", payload.level, payload.message);
            }
            DashboardMessage::AgentMetrics(payload) => {
                // Display as total counts (treating per_min fields as totals until backend is updated)
                self.ui.label(id!(tokens_card.value)).set_text(cx, &format!("{:.0}", payload.tokens_per_min));
                self.ui.label(id!(llm_calls_card.value)).set_text(cx, &format!("{}", payload.llm_calls_per_min as i32));
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
            self.ui.label(id!(plan_list)).set_text(cx, "No plan yet");
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

        self.ui.label(id!(plan_list)).set_text(cx, &plan_text);
    }

    fn update_trace_view(&mut self, cx: &mut Cx) {
        // Build trace text from events
        let mut trace_text = String::new();
        for event in self.trace_events.iter().rev().take(10) {
            let time_str = format_timestamp(event.timestamp);
            match event.event_type.as_str() {
                "fsm_transition" => {
                    let from = event.from_state.as_deref().unwrap_or("?");
                    let to = event.to_state.as_deref().unwrap_or("?");
                    trace_text.push_str(&format!("[{}] FSM: {} → {}\n", time_str, from, to));
                }
                "action_start" | "action_end" => {
                    let action = event.action_name.as_deref().unwrap_or("?");
                    trace_text.push_str(&format!("[{}] Action: {}\n", time_str, action));
                }
                _ => {
                    trace_text.push_str(&format!("[{}] {}\n", time_str, event.event_type));
                }
            }
        }

        if trace_text.is_empty() {
            trace_text = "No trace events yet".to_string();
        }

        self.ui.label(id!(trace_log)).set_text(cx, &trace_text);
        self.ui.redraw(cx);
    }

    fn update_memory_view(&mut self, cx: &mut Cx) {
        if let Some(ref mem) = self.memory_data {
            let text = format!(
                "Total Entries: {}\n\nRecent Queries: {}\nRecent Additions: {}",
                mem.total_entries,
                mem.recent_queries.len(),
                mem.recent_additions.len()
            );
            self.ui.label(id!(memory_info)).set_text(cx, &text);
            self.ui.redraw(cx);
        }
    }

    fn update_game_view(&mut self, cx: &mut Cx) {
        if let Some(ref metrics) = self.game_metrics {
            let text = format!(
                "FPS: {:.1}\nFrame Time: {:.2}ms\nTick Rate: {:.1}\nEntities: {}",
                metrics.fps,
                metrics.frame_time_ms,
                metrics.tick_rate,
                metrics.entity_count
            );
            self.ui.label(id!(game_info)).set_text(cx, &text);
            self.ui.redraw(cx);
        }
    }
}

fn format_timestamp(ts: u64) -> String {
    // Convert milliseconds to seconds for display
    let secs = ts / 1000;
    let mins = secs / 60;
    let secs = secs % 60;
    format!("{:02}:{:02}", mins, secs)
}
