// pixhelp overlay: transparent, click-through, always-on-top toast window.
//
// Talks to the Python backend over WebSocket (specs.md §3.4):
//   connect -> {"type":"hello","schema_version":1,"token":...}
//   backend -> {"type":"toast",...} | {"type":"clear"} | {"type":"ping"}
//
// Token: pass --dart-define=IPC_TOKEN=... (or leave empty for local dev when
// the backend generated an ephemeral token you paste in). Reconnects forever.

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:web_socket_channel/io.dart';
import 'package:window_manager/window_manager.dart';

const ipcHost = String.fromEnvironment('IPC_HOST', defaultValue: '127.0.0.1');
const ipcPort = String.fromEnvironment('IPC_PORT', defaultValue: '8765');
const ipcToken = String.fromEnvironment('IPC_TOKEN', defaultValue: '');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await windowManager.ensureInitialized();
  await windowManager.setWaitUntilFirstLaunchCompleted(false);
  await windowManager.setAlwaysOnTop(true);
  await windowManager.setSkipTaskbar(true);
  await windowManager.setTitleBarStyle(TitleBarStyle.hidden);
  await windowManager.setBackgroundColor(Colors.transparent);
  // Click-through + no-activate: the user must never be blocked by a toast.
  await windowManager.setIgnoreMouseEvents(true);
  await windowManager.focusable(false);
  await windowManager.setBounds(const Rect.fromLTWH(0, 0, 480, 200));
  await _positionBottomRight();
  runApp(const OverlayApp());
}

Future<void> _positionBottomRight() async {
  final display = await windowManager.getPrimaryDisplay();
  final size = display.size;
  const w = 480.0, h = 200.0, margin = 24.0;
  await windowManager.setBounds(
    Rect.fromLTWH(size.width - w - margin, size.height - h - margin - 48, w, h),
  );
}

class OverlayApp extends StatelessWidget {
  const OverlayApp({super.key});

  @override
  Widget build(BuildContext context) {
    return const MaterialApp(
      debugShowCheckedModeBanner: false,
      home: ToastHost(),
    );
  }
}

class ToastMessage {
  ToastMessage(this.title, this.body, this.severity, this.ttl);
  final String title;
  final String body;
  final String severity;
  final double ttl;
}

class ToastHost extends StatefulWidget {
  const ToastHost({super.key});

  @override
  State<ToastHost> createState() => _ToastHostState();
}

class _ToastHostState extends State<ToastHost> {
  IOWebSocketChannel? _channel;
  Timer? _reconnectTimer;
  Timer? _dismissTimer;
  int _backoffMs = 1000;
  ToastMessage? _toast;
  String _status = 'connecting…';

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void dispose() {
    _reconnectTimer?.cancel();
    _dismissTimer?.cancel();
    _channel?.sink.close();
    super.dispose();
  }

  void _connect() {
    final uri = Uri.parse('ws://$ipcHost:$ipcPort');
    final channel = IOWebSocketChannel.connect(
      uri,
      protocols: null,
      connectTimeout: const Duration(seconds: 5),
    );
    _channel = channel;
    channel.sink.add(jsonEncode({
      'type': 'hello',
      'schema_version': 1,
      'token': ipcToken,
    }));
    channel.stream.listen(
      (raw) {
        _backoffMs = 1000;
        setState(() => _status = 'connected');
        _handle(jsonDecode(raw as String) as Map<String, dynamic>);
      },
      onDone: _scheduleReconnect,
      onError: (_) => _scheduleReconnect(),
    );
  }

  void _scheduleReconnect() {
    setState(() => _status = 'reconnecting…');
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(Duration(milliseconds: _backoffMs), _connect);
    _backoffMs = (_backoffMs * 2).clamp(1000, 30000);
  }

  void _handle(Map<String, dynamic> msg) {
    switch (msg['type']) {
      case 'toast':
        final rect = msg['anchor_rect'] as Map<String, dynamic>?;
        _dismissTimer?.cancel();
        setState(() {
          _toast = ToastMessage(
            (msg['title'] ?? '') as String,
            (msg['body'] ?? '') as String,
            (msg['severity'] ?? 'info') as String,
            ((msg['ttl_s'] ?? 8.0) as num).toDouble(),
          );
        });
        _dismissTimer = Timer(
          Duration(milliseconds: ((_toast?.ttl ?? 8) * 1000).round()),
          () => setState(() => _toast = null),
        );
        // rect currently only affects placement priority; see specs.md §5.
        assert(rect == null || rect.length == 4);
        break;
      case 'clear':
        _dismissTimer?.cancel();
        setState(() => _toast = null);
        break;
      case 'ping':
        _channel?.sink.add(jsonEncode({'type': 'pong'}));
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final toast = _toast;
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Align(
        alignment: Alignment.bottomRight,
        child: AnimatedOpacity(
          opacity: toast == null ? 0 : 1,
          duration: const Duration(milliseconds: 250),
          child: toast == null
              ? const SizedBox.shrink()
              : Container(
                  margin: const EdgeInsets.all(16),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                  decoration: BoxDecoration(
                    color: const Color(0xF01E1E2E),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: toast.severity == 'warn'
                          ? const Color(0xFFFFB74D)
                          : const Color(0xFF64B5F6),
                    ),
                  ),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        toast.title,
                        style: const TextStyle(
                          color: Colors.white,
                          fontWeight: FontWeight.w600,
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        toast.body,
                        style: const TextStyle(
                          color: Color(0xFFCCCCCC),
                          fontSize: 13,
                        ),
                      ),
                    ],
                  ),
                ),
        ),
      ),
    );
  }
}
