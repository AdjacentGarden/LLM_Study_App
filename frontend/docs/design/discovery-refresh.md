# Discovery pull-to-refresh

Reference: https://designspells.com/spells/pull-to-refresh-animation-in-snapchat

The search field stays fixed. Overscroll exposes a vertical gradient band, pale
blue at the top and lavender at the bottom, between
search and categories. A cloud peeks over its lower edge, anticipates the release,
then launches upward with a short blue, purple and pink trail. Native
UIRefreshControl owns the release threshold, cancellation and rubber-banding.
During pull and loading, the cloud grips the bottom edge. Its loading movement
breathes around that contact line. Only success switches to the original
raised-hands artwork and launches upward.
Reduced Motion keeps a static cloud and uses spoken refresh announcements.


## Edge-attached loading pose

- Tool: built-in imagegen, transparent background enabled.
- Identity reference: Resources/Content/assets/brand/discovery/cloud-refresh-v1.png
- Asset: Resources/Content/assets/brand/discovery/cloud-refresh-perched-v1.png
- During pull and loading, both hands remain on the exposed region's lower boundary. Loading breathes around that baseline, with no floating or rotation.
- Success replaces the perched pose with the original raised-hands asset and launches it upward; cancellation and failure do not launch.
- The original PNG and its alpha are preserved. The view compensates for transparent canvas margins using the visible artwork baseline (row 798 of the 941px loading asset, row 1040 of the 1254px launch asset).

Prompt:

> Use case: identity-preserve. Asset type: a transparent UI mascot pose for the loading phase of an iOS pull-to-refresh control. Input image 1 is the existing blue cloud IP identity and illustration style reference. Create ONE new pose of exactly the same friendly blue cloud, with the same thick navy outline, soft sky-blue shading, dark glossy eyes and pink cheeks. Pose: the cloud is peeking up from BEHIND a horizontal screen edge at the BOTTOM of the composition, its face visible above the edge, with TWO small rounded blue hands resting on and gripping that SAME bottom edge, one on either side of the face. Both hand bottoms and the visible lower edge of the face must share a single flat horizontal baseline so the drawing can sit flush on a UI boundary. Only the upper half of the cloud is visible, lower body hidden below the imaginary edge. Compact cloud head with happy curious expression, one eye winking, small smile. Hands are DOWN at the bottom baseline, NOT lifted into the air. No platform, ledge, floor, line or border should be drawn: the actual UI supplies the edge. Entire isolated upper-half character and hands fully within the image with small balanced transparent margins, centered. Keep this pose short and wide, roughly 1.8:1 silhouette. Real transparent background, no text, props, bubbles, symbols, trail, cast shadow, extra characters or extra versions. Clean sticker illustration readable at a 64-point width. Preserve the reference character identity; change only the pose and expression.

The discovery catalog currently ships in the app bundle. A refresh reloads that
catalog, preserving the search term and selected category; it makes no server request.

## Generated asset

- Tool: built-in imagegen, transparent background enabled.
- Reference: Resources/Content/assets/brand/ai-assistant/avatar-v1.png
- Asset: Resources/Content/assets/brand/discovery/cloud-refresh-v1.png
- Original generated PNG alpha is preserved.

Prompt:

> Create one production-ready transparent PNG game/UI mascot asset based on the reference image, keeping the exact same blue cloud character identity: rounded puffy sky-blue cloud, thick dark navy outline, two glossy oval dark eyes, small curved eyebrows, rosy pink cheeks, happy open mouth. This is a pose variant for an iOS pull-to-refresh animation. Show the COMPLETE cloud character front-facing, centered, with BOTH tiny arms lifted outward and upward in an eager ready-to-jump pose. No speech bubble, no ellipsis, no text, no props, no background, no floor, no external shadow, no trail. Preserve the reference's clean softly shaded sticker illustration, not 3D plastic. Silhouette should be compact, horizontally slightly wider than tall; fill 85 percent of the square canvas with balanced small transparent margins. Must stay readable at 52 point UI size. Single character only, one pose only, real alpha transparency. Reference is for identity and style, not an image to copy unchanged.
