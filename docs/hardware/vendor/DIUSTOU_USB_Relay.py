#!/usr/bin/env python3
"""
USB Relay (TC, 8, Opto) Test Program - Based on ASCII Multi-line Response
USB 继电器（TC，8路，光耦隔离）测试程序 - 基于 ASCII 多行响应
Command Format: A0 [Address] [Operation] [Checksum] (Binary)
命令格式: A0 [地址] [操作] [校验和] (二进制)
Response Example:
响应示例:
  Single Channel: "CH1:ON" or "CH1:OFF"
  单通道: "CH1:ON" 或 "CH1:OFF"
  All Channels: 8 lines "CHx:ON/OFF"
  全部通道: 8行 "CHx:ON/OFF"
"""

import serial
import serial.tools.list_ports
import sys
import time
import re

class USBRelay:
    def __init__(self, port=None, baudrate=115200, timeout=1):
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self._connect(port)

    def _connect(self, port):
        if port is None:
            ports = list(serial.tools.list_ports.comports())
            if not ports:
                raise RuntimeError("No serial ports detected / 未检测到任何串口设备")
            for p in ports:
                desc = p.description.lower()
                if any(k in desc for k in ["usb", "ch340", "cp210", "ftdi", "serial"]):
                    port = p.device
                    break
            if port is None:
                port = ports[0].device
            print(f"Auto Selected Port: {port} / 自动选择串口: {port}")
        try:
            self.ser = serial.Serial(port, self.baudrate, timeout=self.timeout)
            print(f"Connected to {port} @ {self.baudrate} bps / 已连接 {port} @ {self.baudrate} bps")
        except Exception as e:
            raise RuntimeError(f"Failed to open port {port}: {e} / 无法打开串口 {port}: {e}")

    def _calc_checksum(self, d1, d2, d3):
        return (d1 + d2 + d3) & 0xFF

    def _send_command(self, addr, op):
        """Send command, return ASCII string for query, None for switch command
        发送命令，查询命令返回 ASCII 字符串，开关命令返回 None"""
        data1, data2, data3 = 0xA0, addr, op
        checksum = self._calc_checksum(data1, data2, data3)
        cmd = bytes([data1, data2, data3, checksum])
        print(f"Command Sent: {' '.join(f'{b:02X}' for b in cmd)} / 发送命令: {' '.join(f'{b:02X}' for b in cmd)}")
        self.ser.write(cmd)

        if op == 0x02:      # Query Command / 查询命令
            # Wait for response (max 200ms)
            # 等待设备响应（最多200ms）
            time.sleep(0.2)
            # Read all available data until timeout
            # 读取所有可用数据，直到超时
            raw_data = b''
            while True:
                chunk = self.ser.read(64)
                if not chunk:
                    break
                raw_data += chunk
                time.sleep(0.05)
                if self.ser.in_waiting == 0:
                    break

            if raw_data:
                ascii_str = raw_data.decode('ascii', errors='replace').strip()
                print(f"ASCII Response Received:\n{ascii_str} / 收到 ASCII 响应:\n{ascii_str}")
                return ascii_str
            else:
                print("Warning: No response received / 警告: 未收到任何响应数据")
                return None
        else:
            time.sleep(0.05)
            return None

    def _parse_single_line(self, line):
        """Parse single line e.g. 'CH1:ON' → (channel, state)
        解析单行字符串，如 'CH1:ON' → (通道号, 状态)"""
        match = re.match(r'CH(\d+):(ON|OFF)', line.strip(), re.IGNORECASE)
        if match:
            ch = int(match.group(1))
            state = (match.group(2).upper() == 'ON')
            return ch, state
        return None, None

    def _parse_response(self, ascii_str, expected_channel=None):
        """Parse multi-line response, return dict or single state
        解析多行响应，返回字典或单个状态"""
        if ascii_str is None:
            return None

        lines = ascii_str.splitlines()
        if not lines:
            return None

        status_dict = {}
        for line in lines:
            ch, state = self._parse_single_line(line)
            if ch is not None:
                status_dict[ch] = state

        if expected_channel is not None:
            return status_dict.get(expected_channel, None)
        else:
            if len(status_dict) == 8:
                return status_dict
            else:
                print(f"Warning: Parsed channels {len(status_dict)} ≠ 8, Data: {status_dict} / 警告: 解析通道数 {len(status_dict)} ≠ 8，实际数据: {status_dict}")
                return status_dict

    def open(self, channel):
        if not 1 <= channel <= 8:
            raise ValueError("Channel must be 1~8 / 通道号必须在1~8之间")
        self._send_command(channel, 0x01)

    def close(self, channel):
        if not 1 <= channel <= 8:
            raise ValueError("Channel must be 1~8 / 通道号必须在1~8之间")
        self._send_command(channel, 0x00)

    def open_all(self):
        self._send_command(0x0F, 0x01)

    def close_all(self):
        self._send_command(0x0F, 0x00)

    def query_channel(self, channel):
        """Query single channel, return True=ON False=OFF
        查询单个通道，返回 True=开 False=关"""
        if not 1 <= channel <= 8:
            raise ValueError("Channel must be 1~8 / 通道号必须在1~8之间")
        resp_str = self._send_command(channel, 0x02)
        return self._parse_response(resp_str, expected_channel=channel)

    def query_all(self):
        """Query all channels, return dict {1:bool...}
        查询所有通道，返回状态字典"""
        resp_str = self._send_command(0x0F, 0x02)
        return self._parse_response(resp_str, expected_channel=None)

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("Port closed / 串口已关闭")


def print_help():
    print("""
╔══════════════════════════════════════════════════════════════╗
║   USB Relay Test Program (ASCII Multi-Line)                  ║
║   USB 继电器测试程序 (ASCII 多行版)                          ║
║   Response: CHx:ON / CHx:OFF                                  ║
║   响应格式: CHx:ON / CHx:OFF                                  ║
╚══════════════════════════════════════════════════════════════╝
Commands / 命令:
  open <n>         Turn ON channel n (1-8)      打开通道 n (1-8)
  close <n>        Turn OFF channel n (1-8)     关闭通道 n (1-8)
  open_all         Turn ON all channels         打开所有通道
  close_all        Turn OFF all channels        关闭所有通道
  query <n>        Check channel n status       查询通道 n 状态
  query_all        Check all channels status    查询所有通道状态
  test             Auto test sequence           自动化测试
  help             Show this help               查看帮助
  exit             Exit program                 退出程序
""")


def auto_test(relay):
    print("\n========== Auto Test Started ==========")
    print("========== 自动化测试 开始 ==========\n")
    for ch in range(1, 9):
        print(f"--- Channel {ch} / 通道 {ch} ---")
        relay.open(ch)
        time.sleep(0.3)
        status = relay.query_channel(ch)
        print(f"  State: {'ON' if status else 'OFF'} / 状态: {'开' if status else '关'}")
        relay.close(ch)
        time.sleep(0.3)
        status = relay.query_channel(ch)
        print(f"  State: {'ON' if status else 'OFF'} / 状态: {'开' if status else '关'}\n")

    print("--- All Channels Test / 全部通道测试 ---")
    relay.open_all()
    time.sleep(0.3)
    all_st = relay.query_all()
    if all_st:
        print("All ON Status / 全部打开后状态:", ', '.join(f"CH{i}:{'ON' if v else 'OFF'}" for i, v in all_st.items()))
    relay.close_all()
    time.sleep(0.3)
    all_st = relay.query_all()
    if all_st:
        print("All OFF Status / 全部关闭后状态:", ', '.join(f"CH{i}:{'ON' if v else 'OFF'}" for i, v in all_st.items()))
    print("\n========== Auto Test Finished ==========")
    print("========== 测试完成 ==========\n")


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else None
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    try:
        relay = USBRelay(port, baud)
    except Exception as e:
        print(f"Initialization failed: {e} / 初始化失败: {e}")
        sys.exit(1)

    print_help()
    while True:
        try:
            cmd_line = input("> ").strip().lower()
            if not cmd_line:
                continue
            parts = cmd_line.split()
            cmd = parts[0]

            if cmd in ("exit", "quit"):
                break
            elif cmd == "help":
                print_help()
            elif cmd == "open" and len(parts) == 2:
                relay.open(int(parts[1]))
            elif cmd == "close" and len(parts) == 2:
                relay.close(int(parts[1]))
            elif cmd == "open_all":
                relay.open_all()
            elif cmd == "close_all":
                relay.close_all()
            elif cmd == "query" and len(parts) == 2:
                ch = int(parts[1])
                st = relay.query_channel(ch)
                if st is not None:
                    print(f"Channel {ch} State: {'ON' if st else 'OFF'} / 通道 {ch} 状态: {'开' if st else '关'}")
                else:
                    print("Query Failed / 查询失败")
            elif cmd == "query_all":
                st = relay.query_all()
                if st:
                    print("All Channels Status / 所有通道状态:")
                    for i in range(1, 9):
                        state = st.get(i, None)
                        if state is None:
                            print(f"  Channel {i}: Unknown / 通道 {i}: 未知")
                        else:
                            print(f"  Channel {i}: {'ON' if state else 'OFF'} / 通道 {i}: {'开' if state else '关'}")
                else:
                    print("Query Failed / 查询失败")
            elif cmd == "test":
                auto_test(relay)
            else:
                print("Unknown command, type 'help' for usage / 未知命令，输入 help 查看帮助")
        except KeyboardInterrupt:
            print("\nExiting / 退出")
            break
        except Exception as e:
            print(f"Error: {e} / 错误: {e}")

    relay.disconnect()


if __name__ == "__main__":
    main()