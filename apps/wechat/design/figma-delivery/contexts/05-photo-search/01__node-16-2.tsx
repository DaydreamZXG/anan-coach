const imgIcon = "http://localhost:3845/assets/49fdb9fdd54a12c8ead208b15a462e04f4d2af94.svg";

export default function Component() {
  return (
    <div className="content-stretch flex flex-col items-start p-[8px] relative rounded-[56px] shadow-[0px_0px_0px_0px_rgba(255,255,255,0.1),0px_60px_120px_0px_rgba(0,0,0,0.2)] size-full" data-node-id="16:2" data-name="01 · 相机授权">
      <div aria-hidden className="absolute bg-[#111827] inset-0 pointer-events-none rounded-[56px]" />
      <div className="bg-[#f7f9fc] h-[844px] relative rounded-[48px] shrink-0 w-full" data-node-id="16:3" data-name="Container">
        <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start overflow-clip relative rounded-[inherit] size-full">
          <div className="bg-black flex-[844_0_0] min-h-px relative w-full" data-node-id="16:5" data-name="Container">
            <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center justify-center overflow-clip px-[32px] relative rounded-[inherit] size-full">
              <div className="relative shrink-0" data-node-id="16:23" data-name="Container:margin">
                <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[24px] relative size-full">
                  <div className="bg-[#101828] content-stretch flex items-center justify-center relative rounded-[33554400px] shrink-0 size-[80px]" data-node-id="16:7" data-name="Container">
                    <div className="relative shrink-0 size-[32px]" data-node-id="16:8" data-name="Icon">
                      <img alt="" className="absolute block inset-0 max-w-none size-full" src={imgIcon} />
                    </div>
                  </div>
                </div>
              </div>
              <div className="relative shrink-0" data-node-id="16:24" data-name="Heading 2:margin">
                <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[12px] relative size-full">
                  <p className="[word-break:break-word] font-['Inter:Bold'] font-bold leading-[28px] not-italic relative shrink-0 text-[20px] text-center text-white tracking-[-0.2px] whitespace-nowrap" data-node-id="16:12">
                    需要相机权限
                  </p>
                </div>
              </div>
              <div className="relative shrink-0" data-node-id="16:25" data-name="Paragraph:margin">
                <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-start pb-[40px] relative size-full">
                  <p className="[word-break:break-word] font-['Inter:Medium'] font-medium leading-[22.75px] not-italic relative shrink-0 text-[#99a1af] text-[14px] text-center w-[326px]" data-node-id="16:15">
                    岸岸需要使用相机来拍摄题目和材料，请在系统弹窗中允许授权。
                  </p>
                </div>
              </div>
              <div className="bg-[#00b8db] relative rounded-[24px] shrink-0 w-[326px]" data-node-id="16:17" data-name="Button">
                <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center justify-center py-[16px] relative size-full">
                  <p className="[word-break:break-word] font-['Inter:Bold'] font-bold leading-[24px] not-italic relative shrink-0 text-[16px] text-center text-white whitespace-nowrap" data-node-id="16:18">
                    允许授权
                  </p>
                </div>
              </div>
              <div className="h-[45px] relative shrink-0 w-[30px]" data-node-id="16:20" data-name="Button">
                <div className="bg-clip-padding border-0 border-[transparent] border-solid content-stretch flex flex-col items-center justify-center pt-[24px] relative size-full">
                  <p className="[word-break:break-word] font-['Inter:Bold'] font-bold leading-[21px] not-italic relative shrink-0 text-[#6a7282] text-[14px] text-center whitespace-nowrap" data-node-id="16:21">
                    返回
                  </p>
                </div>
              </div>
            </div>
          </div>
          <div className="absolute bg-black h-[34px] left-[135px] rounded-[20px] top-[12px] w-[120px]" data-node-id="16:4" data-name="Container" />
        </div>
      </div>
      <div className="absolute inset-0 pointer-events-none rounded-[inherit] shadow-[inset_0px_0px_0px_0px_rgba(255,255,255,0.1)]" />
    </div>
  );
}