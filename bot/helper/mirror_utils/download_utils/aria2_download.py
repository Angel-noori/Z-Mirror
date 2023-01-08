from os import remove, path
from time import sleep, time
from bot.helper.mirror_utils.upload_utils.gdriveTools import GoogleDriveHelper
from bot.helper.mirror_utils.status_utils.aria_download_status import AriaDownloadStatus
from bot.helper.ext_utils.fs_utils import (check_storage_threshold, clean_unwanted, get_base_name)
from bot import (LOGGER, aria2, aria2_options, aria2c_global, config_dict, download_dict, download_dict_lock)
from bot.helper.telegram_helper.message_utils import (deleteMessage, sendMessage, sendStatusMessage, update_all_messages)
from bot.helper.ext_utils.bot_utils import (bt_selection_buttons, get_readable_file_size, getDownloadByGid, is_magnet, new_thread)

@new_thread
def __onDownloadStarted(api, gid):
    download = api.get_download(gid)
    if download.is_metadata:
        LOGGER.info(f'onDownloadStarted: {gid} METADATA')
        sleep(1)
        if dl := getDownloadByGid(gid):
            listener = dl.listener()
            if listener.select:
                metamsg = "Downloading Metadata, wait then you can select files. Use torrent file to avoid this wait."
                meta = sendMessage(metamsg, listener.bot, listener.message)
                while True:
                    if download.is_removed or download.followed_by_ids:
                        deleteMessage(listener.bot, meta)
                        break
                    download = download.live
        return
    else:
        LOGGER.info(f'onDownloadStarted: {download.name} - Gid: {gid}')
    try:
        if config_dict['STOP_DUPLICATE']:
            sleep(1)
            if dl := getDownloadByGid(gid):
                listener = dl.listener()
                download = api.get_download(gid)
                if not listener.isLeech and not listener.select:
                    if not download.is_torrent:
                        sleep(3)
                        download = download.live
                    LOGGER.info('Checking File/Folder if already in Drive...')
                    sname = download.name
                    if listener.isZip:
                        sname = f"{sname}.zip"
                    elif listener.extract:
                        try:
                            sname = get_base_name(sname)
                        except:
                            sname = None
                    if sname:
                        smsg, button = GoogleDriveHelper().drive_list(sname, True)
                        if smsg:
                            listener.onDownloadError('File/Folder already available in Drive.\nHere are the search results:\n', button)
                            api.remove([download], force=True, files=True, clean=True)
                            return
        if any([(DIRECT_LIMIT := config_dict['DIRECT_LIMIT']),
                (TORRENT_LIMIT := config_dict['TORRENT_LIMIT']),
                (LEECH_LIMIT := config_dict['LEECH_LIMIT']),
                (STORAGE_THRESHOLD := config_dict['STORAGE_THRESHOLD'])]):
            sleep(1)
            dl = getDownloadByGid(gid)
            if dl and hasattr(dl, 'listener'):
                listener = dl.listener()
            else:
                return
            download = api.get_download(gid)
            if download.total_length == 0:
                start_time = time()
                while time() - start_time <= 15:
                    download = api.get_download(gid)
                    download = download.live
                    if download.followed_by_ids:
                        download = api.get_download(download.followed_by_ids[0])
                    if download.total_length > 0:
                        break
            size = download.total_length
            limit_exceeded = ''
            if not limit_exceeded and STORAGE_THRESHOLD:
                limit = STORAGE_THRESHOLD * 1024**3
                arch = any([listener.isZip, listener.extract])
                acpt = check_storage_threshold(size, limit, arch, True)
                if not acpt:
                    limit_exceeded = f'You must leave {get_readable_file_size(limit)} free storage.'
            if not limit_exceeded and DIRECT_LIMIT and not download.is_torrent:
                limit = DIRECT_LIMIT * 1024**3
                if size > limit:
                    limit_exceeded = f'Direct limit is {get_readable_file_size(limit)}'
            if not limit_exceeded and TORRENT_LIMIT and download.is_torrent:
                limit = TORRENT_LIMIT * 1024**3
                if size > limit:
                    limit_exceeded = f'Torrent limit is {get_readable_file_size(limit)}'
            if not limit_exceeded and LEECH_LIMIT and listener.isLeech:
                limit = LEECH_LIMIT * 1024**3
                if size > limit:
                    limit_exceeded = f'Leech limit is {get_readable_file_size(limit)}'
            if limit_exceeded:
                listener.onDownloadError(f'{limit_exceeded}.\nYour File/Folder size is {get_readable_file_size(size)}')
                api.remove([download], force=True, files=True, clean=True)
                return
    except Exception as e:
        LOGGER.error(f"{e} onDownloadStart: {gid} check duplicate didn't pass")

@new_thread
def __onDownloadComplete(api, gid):
    try:
        download = api.get_download(gid)
    except:
        return
    if download.followed_by_ids:
        new_gid = download.followed_by_ids[0]
        LOGGER.info(f'Gid changed from {gid} to {new_gid}')
        if dl := getDownloadByGid(new_gid):
            listener = dl.listener()
            if config_dict['BASE_URL'] and listener.select:
                api.client.force_pause(new_gid)
                SBUTTONS = bt_selection_buttons(new_gid)
                msg = f"<b>Name</b>: <code>{dl.name()}</code>\n\nYour download paused. Choose files then press Done Selecting button to start downloading."
                sendMessage(msg, listener.bot, listener.message, SBUTTONS)
    elif download.is_torrent:
        if dl := getDownloadByGid(gid):
            if hasattr(dl, 'listener') and dl.seeding:
                LOGGER.info(f"Cancelling Seed: {download.name} onDownloadComplete")
                dl.listener().onUploadError(f"Seeding stopped with Ratio: {dl.ratio()} and Time: {dl.seeding_time()}")
                api.remove([download], force=True, files=True, clean=True)
    else:
        LOGGER.info(f"onDownloadComplete: {download.name} - Gid: {gid}")
        if dl := getDownloadByGid(gid):
            dl.listener().onDownloadComplete()
            api.remove([download], force=True, files=True, clean=True)

@new_thread
def __onBtDownloadComplete(api, gid):
    seed_start_time = time()
    sleep(1)
    download = api.get_download(gid)
    LOGGER.info(f"onBtDownloadComplete: {download.name} - Gid: {gid}")
    if dl := getDownloadByGid(gid):
        listener = dl.listener()
        if listener.select:
            res = download.files
            for file_o in res:
                f_path = file_o.path
                if not file_o.selected and path.exists(f_path):
                    try:
                        remove(f_path)
                    except:
                        pass
            clean_unwanted(download.dir)
        if listener.seed:
            try:
                api.set_options({'max-upload-limit': '0'}, [download])
            except Exception as e:
                LOGGER.error(f'{e} You are not able to seed because you added global option seed-time=0 without adding specific seed_time for this torrent GID: {gid}')
        else:
            try:
                api.client.force_pause(gid)
            except Exception as e:
                LOGGER.error(f"{e} GID: {gid}" )
        listener.onDownloadComplete()
        download = download.live
        if listener.seed:
            if download.is_complete:
                if dl := getDownloadByGid(gid):
                    LOGGER.info(f"Cancelling Seed: {download.name}")
                    listener.onUploadError(f"Seeding stopped with Ratio: {dl.ratio()} and Time: {dl.seeding_time()}")
                    api.remove([download], force=True, files=True, clean=True)
            else:
                with download_dict_lock:
                    if listener.uid not in download_dict:
                        api.remove([download], force=True, files=True, clean=True)
                        return
                    download_dict[listener.uid] = AriaDownloadStatus(gid, listener, True)
                    download_dict[listener.uid].start_time = seed_start_time
                LOGGER.info(f"Seeding started: {download.name} - Gid: {gid}")
                update_all_messages()
        else:
            api.remove([download], force=True, files=True, clean=True)

@new_thread
def __onDownloadStopped(api, gid):
    sleep(6)
    if dl := getDownloadByGid(gid):
        dl.listener().onDownloadError('Dead Torrent! Find Torrent with good Seeders.\n\nYou Can Try With qBittorrent engine.')

@new_thread
def __onDownloadError(api, gid):
    LOGGER.info(f"onDownloadError: {gid}")
    error = "None"
    try:
        download = api.get_download(gid)
        error = download.error_message
        LOGGER.info(f"Download Error: {error}")
    except:
        pass
    if dl := getDownloadByGid(gid):
        dl.listener().onDownloadError(error)

def start_listener():
    aria2.listen_to_notifications(threaded=True,
                                  on_download_start=__onDownloadStarted,
                                  on_download_error=__onDownloadError,
                                  on_download_stop=__onDownloadStopped,
                                  on_download_complete=__onDownloadComplete,
                                  on_bt_download_complete=__onBtDownloadComplete,
                                  timeout=60)

def add_aria2c_download(link: str, path, listener, filename, auth, ratio, seed_time):
    args = {'dir': path, 'max-upload-limit': '1K', 'netrc-path': '/usr/src/app/.netrc'}
    a2c_opt = {**aria2_options}
    [a2c_opt.pop(k) for k in aria2c_global if k in aria2_options]
    args.update(a2c_opt)
    if filename:
        args['out'] = filename
    if auth:
        args['header'] = f"authorization: {auth}"
    if ratio:
        args['seed-ratio'] = ratio
    if seed_time:
        args['seed-time'] = seed_time
    if TORRENT_TIMEOUT := config_dict['TORRENT_TIMEOUT']:
        args['bt-stop-timeout'] = str(TORRENT_TIMEOUT)
    listener.selectCategory()
    if is_magnet(link):
        download = aria2.add_magnet(link, args)
    else:
        download = aria2.add_uris([link], args)
    if download.error_message:
        error = str(download.error_message).replace('<', ' ').replace('>', ' ')
        LOGGER.info(f"Download Error: {error}")
        return sendMessage(error, listener.bot, listener.message)
    with download_dict_lock:
        download_dict[listener.uid] = AriaDownloadStatus(download.gid, listener)
        LOGGER.info(f"Aria2Download started: {download.gid}")
    listener.onDownloadStart()
    if not listener.select:
        sendStatusMessage(listener.message, listener.bot)

start_listener()
