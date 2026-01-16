import asyncio
import aiohttp
from aiohttp import web
import json
import os

# 연결된 클라이언트들
phone_clients = set()
pc_clients = set()

# 배틀그라운드용 클라이언트
gun_clients = set()
pc_battle_clients = set()

# 포트리스용 클라이언트 (기존 2인용)
fortress_controller_clients = {}  # {ws: player_num}
fortress_game_clients = set()

# 포트리스 멀티용 클라이언트 (새 10인용)
fortress_multi_controllers = {}  # {ws: {'player_id': id, 'team': team, 'name': name}}
fortress_multi_games = set()
fortress_multi_state = {
    'players': {},  # {player_id: {'team': 'red'/'blue', 'ready': bool, 'name': str, 'team_size': int}}
    'game_started': False,
    'max_players': 10
}

async def broadcast_multi_player_list():
    """10인용: 모든 게임 클라이언트와 컨트롤러에 플레이어 목록 전송"""
    msg = json.dumps({
        'type': 'player_list',
        'players': fortress_multi_state['players'],
        'game_started': fortress_multi_state['game_started']
    })
    for game in fortress_multi_games.copy():
        try:
            await game.send_str(msg)
        except:
            fortress_multi_games.discard(game)
    for ctrl in list(fortress_multi_controllers.keys()):
        try:
            await ctrl.send_str(msg)
        except:
            del fortress_multi_controllers[ctrl]

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    client_type = None
    my_player_id = None  # 10인용 플레이어 ID 추적

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)

                # 클라이언트 타입 등록
                if data.get('type') == 'phone':
                    phone_clients.add(ws)
                    client_type = 'phone'
                    print(f"폰 연결됨 (총 {len(phone_clients)}대)")

                elif data.get('type') == 'pc':
                    pc_clients.add(ws)
                    client_type = 'pc'
                    print(f"PC 연결됨 (총 {len(pc_clients)}대)")

                # 배틀그라운드 - 건 컨트롤러
                elif data.get('type') == 'gun':
                    gun_clients.add(ws)
                    client_type = 'gun'
                    print(f"Gun 컨트롤러 연결됨 (총 {len(gun_clients)}대)")
                    # PC에 건 연결 알림
                    for pc in pc_battle_clients.copy():
                        try:
                            await pc.send_str(json.dumps({'type': 'gun_connected'}))
                        except:
                            pc_battle_clients.discard(pc)

                # 배틀그라운드 - PC
                elif data.get('type') == 'pc_battle':
                    pc_battle_clients.add(ws)
                    client_type = 'pc_battle'
                    print(f"Battle PC 연결됨 (총 {len(pc_battle_clients)}대)")

                # 건 컨트롤러에서 온 데이터를 Battle PC로 전달
                elif data.get('type') in ['aim', 'fire', 'reload', 'calibrated', 'show_target']:
                    for pc in pc_battle_clients.copy():
                        try:
                            await pc.send_str(msg.data)
                        except:
                            pc_battle_clients.discard(pc)

                # ========== 기존 2인용 포트리스 ==========
                # 포트리스 - 컨트롤러 (2인용)
                elif data.get('type') == 'fortress_controller':
                    player_num = data.get('player', 0)
                    fortress_controller_clients[ws] = player_num
                    client_type = 'fortress_controller'
                    print(f"Fortress P{player_num} 컨트롤러 연결됨 (총 {len(fortress_controller_clients)}대)")
                    # 게임에 플레이어 연결 알림
                    for game in fortress_game_clients.copy():
                        try:
                            await game.send_str(json.dumps({'type': 'player_joined', 'player': player_num}))
                        except:
                            fortress_game_clients.discard(game)

                # 포트리스 - 게임 (PC) (2인용)
                elif data.get('type') == 'fortress_game':
                    fortress_game_clients.add(ws)
                    client_type = 'fortress_game'
                    print(f"Fortress 게임 연결됨 (총 {len(fortress_game_clients)}대)")

                # 포트리스 컨트롤러에서 온 데이터를 게임으로 전달 (2인용)
                elif data.get('type') in ['angle', 'power', 'fire_fortress', 'request_state', 'move', 'player_ready', 'set_game_mode', 'start_game_request', 'restart_game_request']:
                    # player_id가 있으면 10인용, 없으면 2인용
                    if data.get('player_id'):
                        # 10인용으로 전달
                        for game in fortress_multi_games.copy():
                            try:
                                await game.send_str(msg.data)
                            except:
                                fortress_multi_games.discard(game)
                    else:
                        # 2인용으로 전달
                        for game in fortress_game_clients.copy():
                            try:
                                await game.send_str(msg.data)
                            except:
                                fortress_game_clients.discard(game)

                # 포트리스 게임에서 온 데이터를 컨트롤러로 전달 (2인용)
                elif data.get('type') in ['wind', 'turn_change', 'game_start', 'game_restart']:
                    for ctrl in list(fortress_controller_clients.keys()):
                        try:
                            await ctrl.send_str(msg.data)
                        except:
                            del fortress_controller_clients[ctrl]

                # ========== 새 10인용 포트리스 멀티 ==========
                # 포트리스 멀티 - 컨트롤러 연결 (10인용)
                elif data.get('type') == 'fortress_multi_controller':
                    player_id = data.get('player_id', str(id(ws)))
                    team = data.get('team', 'none')
                    name = data.get('name', 'Player')
                    team_size = data.get('team_size', 3)  # 기본값 3

                    fortress_multi_controllers[ws] = {
                        'player_id': player_id,
                        'team': team,
                        'name': name,
                        'team_size': team_size
                    }
                    client_type = 'fortress_multi_controller'
                    my_player_id = player_id

                    # 게임 상태에 플레이어 추가
                    fortress_multi_state['players'][player_id] = {
                        'team': team,
                        'ready': False,
                        'name': name,
                        'team_size': team_size
                    }

                    print(f"Fortress Multi [{team}] {name} (팀원 {team_size}명) 연결됨 (총 {len(fortress_multi_controllers)}명)")
                    await broadcast_multi_player_list()

                # 포트리스 멀티 - 게임 (PC) (10인용)
                elif data.get('type') == 'fortress_multi_game':
                    fortress_multi_games.add(ws)
                    client_type = 'fortress_multi_game'
                    print(f"Fortress Multi 게임 연결됨 (총 {len(fortress_multi_games)}대)")
                    # 현재 플레이어 목록 전송
                    await ws.send_str(json.dumps({
                        'type': 'player_list',
                        'players': fortress_multi_state['players'],
                        'game_started': fortress_multi_state['game_started']
                    }))

                # 포트리스 멀티 - 팀 변경
                elif data.get('type') == 'team_change':
                    player_id = data.get('player_id')
                    team = data.get('team')
                    if player_id in fortress_multi_state['players']:
                        fortress_multi_state['players'][player_id]['team'] = team
                        if ws in fortress_multi_controllers:
                            fortress_multi_controllers[ws]['team'] = team
                        await broadcast_multi_player_list()

                # 포트리스 멀티 - 준비 상태
                elif data.get('type') == 'multi_player_ready':
                    player_id = data.get('player_id')
                    ready = data.get('ready', True)
                    if player_id in fortress_multi_state['players']:
                        fortress_multi_state['players'][player_id]['ready'] = ready
                        await broadcast_multi_player_list()

                # 포트리스 멀티 - 게임 시작 요청
                elif data.get('type') == 'multi_start_game':
                    fortress_multi_state['game_started'] = True
                    start_msg = json.dumps({
                        'type': 'game_start',
                        'players': fortress_multi_state['players']
                    })
                    for game in fortress_multi_games.copy():
                        try:
                            await game.send_str(start_msg)
                        except:
                            fortress_multi_games.discard(game)
                    for ctrl in list(fortress_multi_controllers.keys()):
                        try:
                            await ctrl.send_str(start_msg)
                        except:
                            del fortress_multi_controllers[ctrl]

                # 포트리스 멀티 - 게임 재시작
                elif data.get('type') == 'multi_restart_game':
                    fortress_multi_state['game_started'] = False
                    for pid in fortress_multi_state['players']:
                        fortress_multi_state['players'][pid]['ready'] = False
                    restart_msg = json.dumps({'type': 'game_restart'})
                    for game in fortress_multi_games.copy():
                        try:
                            await game.send_str(restart_msg)
                        except:
                            fortress_multi_games.discard(game)
                    for ctrl in list(fortress_multi_controllers.keys()):
                        try:
                            await ctrl.send_str(restart_msg)
                        except:
                            del fortress_multi_controllers[ctrl]
                    await broadcast_multi_player_list()

                # 포트리스 멀티 게임 데이터 전달
                elif data.get('type') in ['multi_angle', 'multi_power', 'multi_fire', 'multi_move']:
                    for game in fortress_multi_games.copy():
                        try:
                            await game.send_str(msg.data)
                        except:
                            fortress_multi_games.discard(game)

                # 포트리스 멀티 게임에서 컨트롤러로 전달
                elif data.get('type') in ['multi_wind', 'multi_game_state', 'multi_player_hit', 'multi_player_eliminated']:
                    for ctrl in list(fortress_multi_controllers.keys()):
                        try:
                            await ctrl.send_str(msg.data)
                        except:
                            del fortress_multi_controllers[ctrl]

                # 폰에서 온 센서 데이터를 PC로 전달
                elif data.get('type') == 'step':
                    for pc in pc_clients.copy():
                        try:
                            await pc.send_str(msg.data)
                        except:
                            pc_clients.discard(pc)

            elif msg.type == aiohttp.WSMsgType.ERROR:
                print(f'WebSocket 에러: {ws.exception()}')

    finally:
        if client_type == 'phone':
            phone_clients.discard(ws)
            print(f"폰 연결 해제 (남은 폰: {len(phone_clients)}대)")
        elif client_type == 'pc':
            pc_clients.discard(ws)
            print(f"PC 연결 해제 (남은 PC: {len(pc_clients)}대)")
        elif client_type == 'gun':
            gun_clients.discard(ws)
            print(f"Gun 연결 해제 (남은 Gun: {len(gun_clients)}대)")
        elif client_type == 'pc_battle':
            pc_battle_clients.discard(ws)
            print(f"Battle PC 연결 해제 (남은 Battle PC: {len(pc_battle_clients)}대)")
        elif client_type == 'fortress_controller':
            if ws in fortress_controller_clients:
                del fortress_controller_clients[ws]
            print(f"Fortress 컨트롤러 연결 해제 (남은: {len(fortress_controller_clients)}대)")
        elif client_type == 'fortress_game':
            fortress_game_clients.discard(ws)
            print(f"Fortress 게임 연결 해제 (남은: {len(fortress_game_clients)}대)")
        elif client_type == 'fortress_multi_controller':
            if ws in fortress_multi_controllers:
                del fortress_multi_controllers[ws]
            if my_player_id and my_player_id in fortress_multi_state['players']:
                del fortress_multi_state['players'][my_player_id]
                print(f"Fortress Multi 연결 해제 - {my_player_id} (남은: {len(fortress_multi_controllers)}명)")
                asyncio.create_task(broadcast_multi_player_list())
            else:
                print(f"Fortress Multi 연결 해제 (남은: {len(fortress_multi_controllers)}명)")
        elif client_type == 'fortress_multi_game':
            fortress_multi_games.discard(ws)
            print(f"Fortress Multi 게임 연결 해제 (남은: {len(fortress_multi_games)}대)")

    return ws

async def index_handler(request):
    # 기본 페이지는 game.html로 리다이렉트
    raise web.HTTPFound('/game.html')

async def init_app():
    app = web.Application()

    # 라우트 설정
    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', websocket_handler)

    # 정적 파일 서빙 (현재 디렉토리)
    app.router.add_static('/', path=os.path.dirname(__file__) or '.', name='static')

    return app

def main():
    # Render 등 클라우드 환경에서는 PORT 환경변수 사용
    port = int(os.environ.get('PORT', 9000))

    print("=" * 50)
    print("통합 서버 시작 (HTTP + WebSocket)")
    print("=" * 50)
    print(f"포트: {port}")
    print("")
    print("[달리기 게임]")
    print(f"  PC: http://localhost:{port}/game.html")
    print("  폰: [ngrok 주소]/sensor.html")
    print("")
    print("[배틀그라운드]")
    print(f"  PC: http://localhost:{port}/battle.html")
    print("  폰: [ngrok 주소]/gun.html")
    print("")
    print("[포트리스 2인용]")
    print(f"  PC: http://localhost:{port}/fortress.html")
    print("  폰: [ngrok 주소]/fortress_control.html")
    print("")
    print("[포트리스 10인용]")
    print(f"  PC: http://localhost:{port}/fortress_multi.html")
    print("  폰: [ngrok 주소]/fortress_control_multi.html")
    print("=" * 50)

    app = asyncio.run(init_app())
    web.run_app(app, host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
