# クリアロックラジオ（仮）

AI・PC、ゲーム、宮崎の暮らしに関わるニュースを、1回につき3本お届けします。プレーヤーでは字幕で内容を追ったり、聞きたい話題へ移動したりできます。元記事へのリンクは各回に掲載します。配信時刻は未定です。

[クリアロックラジオ（仮）の公開先](https://clear-rock.github.io/my-radio/)

## 制作

`python tools/make_episode.py candidates` で直近7日間の候補を確認します。元記事の本文を読み、採用する3件を `[{"url":"https://…","facts":["確認済みの事実"]}, …]` の形で手元のJSONにまとめます。並びはAI・PC、ゲーム、宮崎です。候補には見出しだけでは判別できない記事も含まれるため、採否は本文で判断します。

`python tools/make_episode.py draft <確認済み記事.json> <台本案.json>` で台本案を作成します。出典と読み上げ表記を確認してから、`python tools/make_episode.py render <台本案.json>` で字幕タイミングと音声を生成します。Python、Ollama（gemma4:12b）、VOICEVOX、FFmpegとFFprobeを使います。

## クレジット

- 読み上げ音声は VOICEVOX:春日部つむぎ
- キャラクターはこのリポジトリに付属するAI生成素材
- 天気データは Open-Meteo（CC BY 4.0）
- プログラムは [MIT License](LICENSE)。台本は CC0
