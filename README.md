# クリアロックラジオ（仮）

AI、PC・ガジェット、ゲーム、宮崎の暮らしに関わるニュースを、1回につき最大4本お届けします。プレーヤーでは字幕で内容を追ったり、聞きたい話題へ移動したりできます。元記事へのリンクは各回に掲載します。配信時刻は未定です。

[クリアロックラジオ（仮）の公開先](https://clear-rock.github.io/my-radio/)

## 制作

[記事選定ルール](tools/article_rules.toml)を参照し、`python tools/make_episode.py candidates` で候補を確認します。AIとPC・ガジェットの候補はPC Watchから取得し、優先条件に近い見出しが先に並びます。`--include-excluded` を付けると、セール・値引きや放送済みURLなどで除外した候補と理由も表示されます。配信先プラットフォームや記事の重要度などは元記事の本文で判断します。

確認した記事を `[{"category":"ai","url":"https://…","facts":["確認済みの事実"],"reason":"採用理由"}, …]` の形で手元のJSONにまとめます。該当記事がない枠は `{"category":"games","reason":"省略理由"}` の形で記録できます。枠のキーは `ai`、`pc`、`games`、`local` です。`python tools/make_episode.py select <確認済み記事.json>` で選定結果を確認できます。AIとPCの両方に入るURLは、採用する枠を明記してください。

記事カードは元記事に移動できる文字カードです。

`python tools/make_episode.py draft <確認済み記事.json> <台本案.json>` で台本案を作成します。出典と読み上げ表記を確認してから、`python tools/make_episode.py render <台本案.json>` で字幕タイミングと音声を生成します。Python、Ollama（gemma4:12b）、VOICEVOX、FFmpegとFFprobeを使います。

## クレジット

- 読み上げ音声は VOICEVOX:春日部つむぎ
- キャラクターはこのリポジトリに付属するAI生成素材
- 天気データは Open-Meteo（CC BY 4.0）
- プログラムは [MIT License](LICENSE)。台本は CC0
