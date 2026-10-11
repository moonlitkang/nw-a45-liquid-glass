"""
The Liquid Glass palette and the exact QML source substitutions.

Design constraints that shaped every value below
------------------------------------------------
* Target panel is roughly 350x580 device pixels inside an 854x480 screen, so
  translucency has to stay high enough for text contrast.
* The stock `Dialog.qml` already dims the whole screen with black at 80%, so
  an extra full-screen scrim must stay light (25%) or the UI goes muddy.
* No blur. Qt Quick on this SoC would need a shader or `layer.enabled`, both of
  which cost a full-screen offscreen pass. The glass look comes from alpha +
  radius + a 1px rim highlight instead.
* Alpha lives in the colour (`#AARRGGBB`) rather than in an `opacity`
  property. `opacity < 1` on an item that has children is group opacity in
  Qt Quick and can force an offscreen FBO composite.
* Every replacement must make the file *smaller*, because the QML lives in a
  fixed-size slot inside the binary. Japanese/CJK comments are stripped for
  exactly that reason - they cost 2-3 bytes per character in UTF-8.
"""

# ---------------------------------------------------------------- palette
GLASS = {
    "panel":      "#cc0e2033",  # 80%  main menu panel
    "panel_soft": "#c70e2033",  # 78%  context menu body
    "row":        "#ad122842",  # 68%  idle row
    "row_sel":    "#e01e4a72",  # 88%  selected row
    "row_sel_ctx": "#e61e4a72", # 90%  selected row (context menu)
    "title":      "#d1102438",  # 82%  context menu title bar
    "scrim":      "#400a1420",  # 25%  screen dim behind a menu
    "separator":  "#803a5a7a",  # 50%  hairline separators
    "rim":        "#4a7a9a",    # panel rim
    "rim_hot":    "#6abaea",    # selected row rim
}

# Theme colour written into ScreenSettings.json (same byte length as #000000)
THEME_BG_OLD = '"D1": "#000000"'
THEME_BG_NEW = '"D1": "#0a1622"'
THEME_BG_OLD_V = '"V1": "#000000"'
THEME_BG_NEW_V = '"V1": "#0a1622"'

NAV_BAR_OLD = '    color: "black"'
NAV_BAR_NEW = '    color: "#0a1622"'

# ------------------------------------------------------------ substitutions
# key   = qrc basename
# value = list of (exact_source_snippet, replacement_snippet)
REPLACEMENTS = {
    "OptionMenu.qml": [(
        """    Rectangle {
        id: opacityArea
        anchors { fill: parent; bottomMargin: p.bottomSpace; }  // NavigationBarの高さ分考慮
        opacity: 0.0""",
        """    Rectangle {
        id: opacityArea
        anchors { fill: parent; bottomMargin: p.bottomSpace; }
        color: "#400a1420"  // 25% alpha; Dialog.qml already dims with black@0.8""",
    )],

    "OptionMenuList.qml": [
        ("""    BorderImage {
        id: backgroundImage
        anchors { top: parent.top; bottom: parent.bottom; right: parent.right; left: list.left; }
        source: p.option_background_parts
        border { top: p.image_border; bottom: p.image_border; right: p.image_border; left: p.image_border; }
        horizontalTileMode: BorderImage.Stretch
        verticalTileMode: BorderImage.Stretch
    }""",
         """    Rectangle {
        id: backgroundImage
        anchors { top: parent.top; bottom: parent.bottom; right: parent.right; left: list.left; }
        color: "#cc0e2033"
        radius: 18
        border.color: "#4a7a9a"
        border.width: 1
    }"""),
        ("""            BorderImage {
                id: listImage
                anchors.fill: parent
                height: p.cell_height
                source: p.option_button_normal_parts
                border { left: p.image_border; right: p.image_border; }
                horizontalTileMode: BorderImage.Stretch
                verticalTileMode: BorderImage.Stretch
                smooth: false
            }""",
         """            Rectangle {
                id: listImage
                anchors.fill: parent
                height: p.cell_height
                color: "#ad122842"
                radius: 10
            }"""),
        ("""            BorderImage {
                id: highlightImage
                anchors.fill: parent
                height: p.cell_height
                visible: index === list.currentIndex
                source: p.option_button_pressed_parts
                border { left: p.image_border; right: p.image_border; }
                horizontalTileMode: BorderImage.Stretch
                verticalTileMode: BorderImage.Stretch
                smooth: false
            }""",
         """            Rectangle {
                id: highlightImage
                anchors.fill: parent
                height: p.cell_height
                visible: index === list.currentIndex
                color: "#e01e4a72"
                radius: 10
                border.color: "#6abaea"
                border.width: 1
            }"""),
        ("""            BorderImage {
                id: separator
                anchors { bottom: parent.bottom; left: parent.left; right: parent.right; }
                height: p.separator_height
                visible: (base.model.count - 1 > index)
                source: p.option_separator_parts
                border { left: p.image_border; right: p.image_border; }
                horizontalTileMode: BorderImage.Stretch
                verticalTileMode: BorderImage.Stretch
                smooth: false
            }""",
         """            Rectangle {
                id: separator
                anchors { bottom: parent.bottom; left: parent.left; right: parent.right; }
                height: p.separator_height
                visible: (base.model.count - 1 > index)
                color: "#803a5a7a"
            }"""),
    ],

    "ContextMenu.qml": [(
        """        Image {
            id: titleButton
            width: parent.width
            height: parent.height
            source: p.popup_area_top
        }""",
        """        Rectangle {
            id: titleButton
            width: parent.width
            height: parent.height
            color: "#d1102438"
            radius: 14
        }""",
    )],

    "ContextMenuList.qml": [
        ("""    BorderImage {
      id: centerImage
      source: p.popup_area_lower // 361 x 89
      width: listArea.width
      height: listArea.height - cellHeight - separatorHeight + bottomMargin
      border { top: 3; left: 3; right: 3; bottom: 3; }
    }""",
         """    Rectangle {
      id: centerImage
      width: listArea.width
      height: listArea.height - cellHeight - separatorHeight + bottomMargin
      color: "#c70e2033"
      radius: 14
    }"""),
        ("""    Image {
        id: footer
        anchors {top: centerImage.bottom; right: centerImage.right;}
        width: listArea.width
        height: cellHeight + separatorHeight // 底部用画像のサイズは89
        source: p.popup_area_lower
    }""",
         """    Rectangle {
        id: footer
        anchors {top: centerImage.bottom; right: centerImage.right;}
        width: listArea.width
        height: cellHeight + separatorHeight
        color: "#c70e2033"
    }"""),
        ("""                    Image {
                        anchors.fill: parent
                        source: p.context_normal
                    }""",
         """                    Rectangle {
                        anchors.fill: parent
                        color: "#ad122842"
                        radius: 8
                    }"""),
        ("""                    Image {
                        anchors.fill: parent
                        source: p.context_pressed
                    }""",
         """                    Rectangle {
                        anchors.fill: parent
                        color: "#e61e4a72"
                        radius: 8
                    }"""),
        ("""            Image {
                anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
                visible: (index===list.count-1)? false : true
                height: separatorHeight
                source: p.context_separator
            }""",
         """            Rectangle {
                anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
                visible: (index===list.count-1)? false : true
                height: separatorHeight
                color: "#803a5a7a"
            }"""),
    ],

    "NavigationBar.qml": [(NAV_BAR_OLD, NAV_BAR_NEW)],
}

# Constructs that must never appear in patched output. Any of these would cost
# an offscreen render pass on the target SoC, or is simply a bug waiting to
# happen in a fixed-size slot.
BANNED_TOKENS = (
    b"FastBlur", b"GaussianBlur", b"DropShadow", b"ShaderEffect",
    b"layer.enabled", b"opacity:",
)

# Background PNGs that are pure black in the stock theme. Making them
# transparent lets the recoloured global theme show through instead.
TRANSPARENT_PNGS = {
    "list_full_normal.png":            (0, 0, 0, 0),
    "list_full_pressed.png":           (170, 210, 255, 60),
    "panel_controller_background.png": (0, 0, 0, 0),
}
