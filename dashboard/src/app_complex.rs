use makepad_widgets::*;
use makepad_component::*;
use crate::ws_client::{start_ws_client, ClientAction, DashboardMessage};
use std::sync::mpsc::{channel, Receiver};

live_design! {
    use link::theme::*;
    use link::shaders::*;
    use link::widgets::*;
    use makepad_component::theme::*;
    use makepad_component::widgets::*;

    // Status Badge Component
    StatusBadge = <MpBadge> {
        padding: {left: 12, right: 12, top: 6, bottom: 6}
        draw_bg: {
            radius: 12.0
            color: #00c853
        }
        draw_text: {
            text_style: <THEME_FONT_BOLD> { font_size: 11.0 }
            color: #fff
        }
    }

    // Card Component
    Card = <View> {
        width: Fill,
        height: Fit,
        margin: {bottom: 12}
        padding: 16,
        show_bg: true,
        draw_bg: {
            color: #252530
            fn pixel(self) -> vec4 {
                let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                sdf.box(1.0, 1.0, self.rect_size.x - 2.0, self.rect_size.y - 2.0, 8.0);
                sdf.fill(self.color);
                return sdf.result;
            }
        }
    }

    // Define LeftPanel with card design
    LeftPanel = <View> {
        width: 280,
        height: Fill,
        show_bg: true,
        draw_bg: { color: #1e1e2e }
        flow: Down,
        padding: 16,
        spacing: 0,

        // Header
        <View> {
            width: Fill,
            height: Fit,
            margin: {bottom: 16}

            <Label> {
                text: "FSM STATE",
                draw_text: {
                    color: #6c7086,
                    text_style: { font_size: 11.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
                }
            }
        }

        // FSM State Card
        <Card> {
            flow: Down,
            spacing: 8,

            <Label> {
                text: "Current State",
                draw_text: {
                    color: #89b4fa,
                    text_style: { font_size: 12.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
                }
            }

            current_state = <Label> {
                text: "IDLE",
                draw_text: {
                    color: #cdd6f4,
                    text_style: { font_size: 20.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-Bold.ttf")} }
                }
            }
        }

        // Goal Card
        <Card> {
            flow: Down,
            spacing: 8,

            <Label> {
                text: "Current Goal",
                draw_text: {
                    color: #89b4fa,
                    text_style: { font_size: 12.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
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

        // Progress Card
        <Card> {
            flow: Down,
            spacing: 8,

            <Label> {
                text: "Progress",
                draw_text: {
                    color: #89b4fa,
                    text_style: { font_size: 12.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
                }
            }

            step_info = <Label> {
                text: "0 / 0",
                draw_text: {
                    color: #f9e2af,
                    text_style: { font_size: 16.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-Bold.ttf")} }
                }
            }
        }
    }

    // Define RightPanel with modern design
    RightPanel = <View> {
        width: 300,
        height: Fill,
        show_bg: true,
        draw_bg: { color: #1e1e2e }
        flow: Down,
        padding: 16,
        spacing: 0,

        // Header
        <View> {
            width: Fill,
            height: Fit,
            margin: {bottom: 16}

            <Label> {
                text: "CONNECTION",
                draw_text: {
                    color: #6c7086,
                    text_style: { font_size: 11.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
                }
            }
        }

        // Connection Status Card
        <Card> {
            flow: Down,
            spacing: 12,

            <View> {
                width: Fill,
                flow: Right,
                align: {y: 0.5},
                spacing: 10,

                <View> {
                    width: 12,
                    height: 12,
                    show_bg: true,
                    draw_bg: {
                        color: #f38ba8
                        fn pixel(self) -> vec4 {
                            let sdf = Sdf2d::viewport(self.pos * self.rect_size);
                            sdf.circle(6.0, 6.0, 5.0);
                            sdf.fill(self.color);
                            return sdf.result;
                        }
                    }
                }

                status = <Label> {
                    text: "Disconnected",
                    draw_text: {
                        color: #f38ba8,
                        text_style: { font_size: 14.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
                    }
                }
            }

            <Label> {
                text: "ws://127.0.0.1:8080",
                draw_text: {
                    color: #6c7086,
                    text_style: { font_size: 11.0 }
                }
            }
        }
    }

    // Define TabView with modern tabs
    TabView = <View> {
        width: Fill,
        height: Fill,
        flow: Down,
        show_bg: true,
        draw_bg: { color: #181825 }
        spacing: 0,

        // Tab bar
        <View> {
            width: Fill,
            height: 48,
            flow: Right,
            spacing: 4,
            padding: {left: 16, right: 16, top: 8, bottom: 0},
            show_bg: true,
            draw_bg: { color: #1e1e2e }

            agent_tab = <MpButton> {
                text: "Agent Benchmark",
                padding: {left: 20, right: 20, top: 10, bottom: 10}
            }

            trace_tab = <MpButton> {
                text: "Trace",
                padding: {left: 20, right: 20, top: 10, bottom: 10}
            }

            memory_tab = <MpButton> {
                text: "Memory",
                padding: {left: 20, right: 20, top: 10, bottom: 10}
            }

            game_tab = <MpButton> {
                text: "Game",
                padding: {left: 20, right: 20, top: 10, bottom: 10}
            }
        }

        // Content area
        <View> {
            width: Fill,
            height: Fill,
            padding: 20,

            agent_content = <View> {
                width: Fill,
                height: Fill,
                visible: true,
                flow: Down,
                spacing: 16,

                <Label> {
                    text: "Agent Benchmark",
                    draw_text: {
                        color: #cdd6f4,
                        text_style: { font_size: 22.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-Bold.ttf")} }
                    }
                }

                <Label> {
                    text: "Real-time agent performance metrics will appear here",
                    draw_text: {
                        color: #6c7086,
                        text_style: { font_size: 14.0 }
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
                spacing: 0,

                // Modern Top Bar with gradient
                <View> {
                    width: Fill,
                    height: 60,
                    padding: {left: 24, right: 24},
                    flow: Right,
                    align: {y: 0.5},
                    show_bg: true,
                    draw_bg: {
                        color: #1e1e2e
                        fn pixel(self) -> vec4 {
                            return self.color;
                        }
                    }

                    <View> {
                        flow: Right,
                        align: {y: 0.5},
                        spacing: 12,

                        <Label> {
                            text: "THE SEED",
                            draw_text: {
                                color: #89b4fa,
                                text_style: { font_size: 20.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-Bold.ttf")} }
                            }
                        }

                        <Label> {
                            text: "OpenRA Agent Dashboard",
                            draw_text: {
                                color: #6c7086,
                                text_style: { font_size: 14.0 }
                            }
                        }
                    }

                    <View> { width: Fill }

                    version_label = <MpBadge> {
                        text: "v0.2.0",
                        padding: {left: 10, right: 10, top: 4, bottom: 4}
                        draw_bg: {
                            radius: 8.0
                            color: #313244
                        }
                        draw_text: {
                            text_style: { font_size: 10.0 }
                            color: #6c7086
                        }
                    }
                }

                // Main Content Area (Three Panels) with separators
                <View> {
                    width: Fill,
                    height: Fill,
                    flow: Right,
                    spacing: 1,

                    left_panel = <LeftPanel> {}

                    <View> { width: 1, height: Fill, show_bg: true, draw_bg: { color: #313244 } }

                    center_panel = <TabView> {}

                    <View> { width: 1, height: Fill, show_bg: true, draw_bg: { color: #313244 } }

                    right_panel = <RightPanel> {}
                }

                // Modern Bottom Command Bar
                <View> {
                    width: Fill,
                    height: 64,
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
                            text_style: { font_size: 13.0, font: {path: dep("crate://makepad-widgets/resources/IBMPlexSans-SemiBold.ttf")} }
                        }
                    }

                    command_input = <MpInput> {
                        width: Fill,
                        height: 40,
                        text: ""
                        placeholder: "Enter command here..."
                    }

                    send_button = <MpButton> {
                        text: "Send",
                        padding: {left: 24, right: 24, top: 10, bottom: 10}
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
            DashboardMessage::GameMetrics(payload) => {
                // Update game performance metrics
                self.ui.label(id!(fps_card.value)).set_text(cx, &format!("{:.1}", payload.fps));
                self.ui.label(id!(frame_time_card.value)).set_text(cx, &format!("{:.2}", payload.frame_time_ms));
                self.ui.label(id!(tick_rate_card.value)).set_text(cx, &format!("{:.1}", payload.tick_rate));
                self.ui.label(id!(entity_count_card.value)).set_text(cx, &format!("{}", payload.entity_count));

                self.ui.redraw(cx);
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
