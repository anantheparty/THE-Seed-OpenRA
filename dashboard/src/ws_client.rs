use serde::{Deserialize, Serialize};
use std::sync::mpsc::Sender;
use std::thread;
use std::time::Duration;
use tungstenite::{connect, Message};
use url::Url;

#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(tag = "type", content = "payload")]
pub enum DashboardMessage {
    #[serde(rename = "init")]
    Init(FsmStatePayload),
    #[serde(rename = "update")]
    Update(FsmStatePayload),
    #[serde(rename = "log")]
    Log(LogPayload),
    #[serde(rename = "agent_metrics")]
    AgentMetrics(AgentMetricsPayload),
    #[serde(rename = "game_metrics")]
    GameMetrics(GameMetricsPayload),
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct FsmStatePayload {
    pub fsm_state: String,
    pub step_index: i32,
    pub plan_length: usize,
    pub current_goal: String,
    pub blackboard: BlackboardData,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct BlackboardData {
    pub game_basic_state: String,
    pub scratchpad: String,
    pub current_step: serde_json::Value,
    pub plan: Vec<serde_json::Value>,
    pub action_result: serde_json::Value,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct LogPayload {
    pub level: String,
    pub message: String,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct AgentMetricsPayload {
    pub tokens_per_min: f64,
    pub llm_calls_per_min: f64,
    pub active_tasks: usize,
    pub total_actions: usize,
    pub execution_volume: usize,
    pub failure_rate: f64,
    pub recovery_rate: f64,
    pub timestamp: u64,
}

#[derive(Serialize, Deserialize, Debug, Clone, Default)]
pub struct GameMetricsPayload {
    pub fps: f64,
    pub frame_time_ms: f64,
    pub tick_rate: f64,
    pub entity_count: usize,
}

pub enum ClientAction {
    Message(DashboardMessage),
    Connected,
    Disconnected,
    Error(String),
}

pub fn start_ws_client(url: String, sender: Sender<ClientAction>, signal: Box<dyn Fn() + Send>) {
    thread::spawn(move || {
        loop {
            match connect(Url::parse(&url).unwrap()) {
                Ok((mut socket, _)) => {
                    sender.send(ClientAction::Connected).unwrap();
                    signal();
                    
                    loop {
                                match socket.read() {
                                    Ok(msg) => {
                                        if let Message::Text(text) = msg {
                                            if let Ok(parsed) = serde_json::from_str::<DashboardMessage>(&text) {
                                                sender.send(ClientAction::Message(parsed)).unwrap();
                                                signal();
                                            }
                                        }
                                    }
                                    Err(_) => {
                                        // sender.send(ClientAction::Error(e.to_string())).unwrap();
                                        // signal();
                                        break;
                                    }
                                }
                            }
                }
                Err(e) => {
                    // sender.send(ClientAction::Error(format!("Connection failed: {}", e))).unwrap();
                    // signal();
                    thread::sleep(Duration::from_secs(2));
                }
            }
        }
    });
}
