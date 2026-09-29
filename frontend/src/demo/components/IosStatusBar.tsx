type IosStatusBarProps = {
  style?: "dark" | "light";
};

/**
 * A non-interactive iPhone status-bar simulation for the mobile demo shell.
 * The values stay deterministic so screenshots and walkthroughs remain stable.
 */
export function IosStatusBar({ style = "dark" }: IosStatusBarProps) {
  return (
    <div
      className="ios-status-bar"
      data-status-bar-style={style}
      data-testid="ios-status-bar"
      aria-hidden="true"
    >
      <span className="ios-status-bar__time-group">
        <span className="ios-status-bar__time">9:41</span>
      </span>
      <span className="ios-status-bar__indicators">
        <svg
          className="ios-status-bar__cellular"
          viewBox="0 0 19.2 12.2264"
          focusable="false"
        >
          <path
            fill="currentColor"
            fillRule="evenodd"
            clipRule="evenodd"
            d="M19.2 1.14622C19.2.513179 18.7224 0 18.1333 0h-1.0666C16.4776 0 16 .513179 16 1.14622v9.93398c0 .633.4776 1.1462 1.0667 1.1462h1.0666c.5891 0 1.0667-.5132 1.0667-1.1462V1.14622ZM11.7659 2.44528h1.0667c.5891 0 1.0666.5255 1.0666 1.17373v7.43369c0 .6482-.4775 1.1737-1.0666 1.1737h-1.0667c-.5891 0-1.0667-.5255-1.0667-1.1737V3.61901c0-.64823.4776-1.17373 1.0667-1.17373ZM7.43411 5.09433H6.36745c-.58911 0-1.06667.53219-1.06667 1.18868v4.75469c0 .6565.47756 1.1887 1.06667 1.1887h1.06666c.58911 0 1.06667-.5322 1.06667-1.1887V6.28301c0-.65649-.47756-1.18868-1.06667-1.18868ZM2.13333 7.53962H1.06667C.477563 7.53962 0 8.06421 0 8.71132v2.34338c0 .6471.477563 1.1717 1.06667 1.1717h1.06666c.58911 0 1.06667-.5246 1.06667-1.1717V8.71132c0-.64711-.47756-1.1717-1.06667-1.1717Z"
          />
        </svg>
        <svg
          className="ios-status-bar__wifi"
          viewBox="0 0 17.1417 12.3283"
          focusable="false"
        >
          <path
            fill="currentColor"
            fillRule="evenodd"
            clipRule="evenodd"
            d="M8.5713 2.46628c2.4871.00011 4.8791.92219 6.6816 2.57567.1358.12765.3527.12604.4864-.00361l1.2975-1.26347a.344.344 0 0 0 .1049-.24752.347.347 0 0 0-.1079-.24631C12.3028-1.09368 4.83907-1.09368.108056 3.28104a.347.347 0 0 0-.108048.24624.344.344 0 0 0 .104681.24759l1.297861 1.26347c.1336.12985.35072.13146.48638.00361C3.69167 3.38836 6.08395 2.46628 8.5713 2.46628Zm-.00335 4.22028c1.35732-.00009 2.66615.51165 3.67235 1.43578.136.13116.3504.12831.4831-.00641l1.2872-1.3193a.369.369 0 0 0 .1045-.26063.37.37 0 0 0-.1097-.25843c-3.0638-2.89085-7.80852-2.89085-10.87235 0a.37.37 0 0 0-.1096.2585.369.369 0 0 0 .10477.26056l1.28691 1.3193c.13265.13472.34702.13757.4831.00641 1.00545-.92352 2.31329-1.43521 3.66972-1.43578Zm2.52445 2.79355a.392.392 0 0 1-.1025.28073l-2.17663 2.45476a.325.325 0 0 1-.24157.1127.325.325 0 0 1-.24157-.1127L6.1531 9.76084a.402.402 0 0 1 .01054-.55655c1.3901-1.31389 3.42602-1.31389 4.81616 0a.403.403 0 0 1 .1126.27582Z"
          />
        </svg>
        <span className="ios-status-bar__battery">
          <span className="ios-status-bar__battery-border" />
          <svg
            className="ios-status-bar__battery-cap"
            viewBox="0 0 1.32804 4.07547"
            focusable="false"
          >
            <path
              fill="currentColor"
              d="M0 0v4.07547c.804731-.34517 1.32804-1.14813 1.32804-2.03773C1.32804 1.14813.804731.345169 0 0Z"
            />
          </svg>
          <span className="ios-status-bar__battery-level" />
        </span>
      </span>
    </div>
  );
}
