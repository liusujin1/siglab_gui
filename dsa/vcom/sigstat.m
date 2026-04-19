% sigstat.mi                                                 10-Oct-00
% 
% Load code to SigLabs and displays status information

function sigstat(Action,In1);

global VI_DEBUG;
global SIGh;          % Handle vector
global SIGnb;         % Number of SigLab boxes found
global SIGb;          % Status info for all SigLabs

%include
% vcol_h.m                                                   
%%%%%%%%%%%%%%%%%%%%%%%%%end_include

%include
% props.m                      Paul Mennen                         10-Mar-99
%%%%%%%%%%%%%%%end_include

%define

% Indices to SIGb array ----------------------------------------------------
%BXid = 1;
%BXmodel, %BXserial, %BXprom, %BXeerom, %BXdram, %BXsram;
%BXin1, %BXin2, %BXout1, %BXout2,%BXnd;

% Actions -------------------------------------------------------------------
%ACT_init = 0;
%ACT_box, %ACT_reload, %ACT_quit, %ACT_set, %ACT_abrt, %ACT_sreq;
%ACTinit   = CALLBK,$$sigstat(ACT_init)$$;   % initialize sigstat
%ACTbox    = CALLBK,$$sigstat(ACT_box)$$;    % SigLab box select
%ACTreload = CALLBK,$$sigstat(ACT_reload)$$; % Reload
%ACTquit   = CALLBK,$$sigstat(ACT_quit)$$;   % Quit
%ACTscsi   = CALLBK,$$sigstat(ACT_set,1)$$;  % Enable SCSI communications
%ACTtext   = CALLBK,$$sigstat(ACT_set,2)$$;  % Echo commands (Text)
%ACThex    = CALLBK,$$sigstat(ACT_set,3)$$;  % Echo commands (Hex)
%ACTstat   = CALLBK,$$sigstat(ACT_set,4)$$;  % Echo status
%ACTabrt   = CALLBK,$$sigstat(ACT_abrt)$$;   % Enable aborts
%ACTsreq   = CALLBK,$$sigstat(ACT_sreq)$$;   % Enable status requests

% Handle index definitions -------------------------------------------------
%SCSI = 1;               % Enable SCSI check box
%TXT,%HEX,%STAT,%ABRT,%SREQ; % Text,hex,status,abort,and sreq check boxes
%IO,%RAM,%ROM,%SER,%MOD;     % SigLab box dependent text objects
%BOX;                    % SigLab box number popup
%FIG;                    % Main figure

% Control positioning ------------------------------------------------------
%VS   = 24;             % Vertical spacing
%WIDc = 200;            % Width, check boxes
%HIGc = 18;             % Height, check boxes
%HIGt = 17;             % Height, text
%HIGb = 20;             % Height, buttons
%X0   = 5;              % x pos, frame edge
%X1   = 10;             % x pos, check boxes
%Y1   = 4;              % y pos, quit button
%Y1a  = Y1 + 35;        % y pos, status
%Y1b  = Y1a + VS;       % y pos, hex
%Y2   = Y1b + VS;       % y pos, text
%Y3   = Y2 + VS;        % y pos, sreq
%Y4   = Y3 + VS;        % y pos, abort
%Y5   = Y4 + VS;        % y pos, scsi
%Y6   = Y5 + VS + 15;   % y pos, box related info
%Y7   = Y6 + 5*HIGc-1;  % y pos, box number popup
%Y8   = Y7 + HIGb + 8;  % y pos, .dll/.out (ver/dat), ch/bw
%Y9   = Y8 + VS+2*HIGc; % y pos, Card/Drive/WinAspi status
%Y11  = Y9 + 2*HIGc+27; % y pos, top of figure

%posFIG   = [430,30,WIDc+20,Y11];           % figure postion
%posFRM   = [X0,Y1a-7,WIDc+10,7*VS-17];     % option frame
%posFRM2  = [X0,Y6-7,WIDc+10,Y11-Y6+2];     % status frame

%posMISC  = [X0,Y1+2,95,HIGt];              % misc text (left of quit button)
%posRL    = [X0+100,Y1,60,HIGb];            % reload
%posQUIT  = [X0+165,Y1,35,HIGb];            % quit
%posSTAT  = [X1,Y1a,WIDc,HIGc];             % stat
%posHEX   = [X1,Y1b,WIDc,HIGc];             % hex
%posTXT   = [X1,Y2,WIDc,HIGc];              % text
%posSREQ  = [X1,Y3,WIDc,HIGc];              % sreq
%posABRT  = [X1,Y4,WIDc,HIGc];              % abort
%posSCSI  = [X1,Y5,WIDc,HIGc];              % scsi
%posIO    = [X1,Y6,WIDc,HIGt];              % Box: IO channel numbers
%posRAM   = [X1,Y6+HIGt,WIDc,HIGt];         % Box: ram
%posVrom  = [X1,Y6+2*HIGt,WIDc,HIGt];       % Box: prom version/eerom stat
%posSN    = [X1,Y6+3*HIGt,WIDc,HIGt];       % Box: serial number
%posMDL   = [X1,Y6+4*HIGt,WIDc,HIGt];       % Box: model number
%posBOX   = [X1+15,Y7,WIDc-30,HIGb];        % Box popup
%posBW    = [X1,Y8,WIDc,HIGt];              % chans / bandwidth
%posVOut  = [X1,Y8+HIGt,WIDc,HIGt];         % siglab.out version
%posVdll  = [X1,Y8+2*HIGt,WIDc,HIGt];       % DLL version
%posWAspi = [X1,Y9,WIDc,HIGt];              % WinAspi
%posDrvr  = [X1,Y9+HIGt,WIDc,HIGt];         % Driver
%posCard  = [X1,Y9+2*HIGt,WIDc,HIGt];       % Card
%end_define

if nargin<1 Action=0; end;   % If no arguments, initialize
if Action == 0               % initialize 
  load vi_color;  colors = stored_vi_colors;
  if isempty(VI_DEBUG) VI_DEBUG = 0;  end;
  if nargin>1 VI_DEBUG = In1; end;   % use value supplied, if any
  [drv,ppath] = pathfind('vbin'); 
  SIGb = zeros(7,11);            % storage for up to 7 boxes
  % now load the SigLabs if they are not alreadly loaded
  [nc outnc bw dllver] = siglab('IOinit',[drv,ppath,'\siglab.out'],VI_DEBUG);
  if nc == 0  tmsg('No SigLabs found; SCSI disabled');
              VI_DEBUG = VI_DEBUG + 1 - rem(VI_DEBUG,2);
              if VI_DEBUG == 1  VI_DEBUG = 3; end;
  end;
  SaveFont = uifont(VIfont);
  SIGh(13) = figure('color',colors(1,:),'NumberTitle','off','menu','none','BackingStore','off','PaperPositionMode','auto','InvertHardcopy','off','PaperOrientation','landscape','PaperUnits','normalized','pos',[430,30,220,438],'Name','SigStat');
  aboEna = siglab('debug',-42);
  uicontrol('style','frame','background',colors(2,:),'pos',[5,32,210,151]);    % frames the check boxes
  uicontrol('style','frame','background',colors(2,:),'pos',[5,191,210,242]);   % frames the status text boxes
  SIGh(1) = uicontrol('style','checkbox','background',colors(13,:),'foreground',colors(19,:),'pos',[10,159,200,18],'Value',rem(VI_DEBUG,2)<1,...
                              'str','Enable SCSI','call','sigstat(4,1)');
  SIGh(2)  = uicontrol('style','checkbox','background',colors(13,:),'foreground',colors(19,:),'pos',[10,87,200,18], 'Value',rem(VI_DEBUG,4)>=2,...
                              'str','Echo commands (text)','call','sigstat(4,2)');
  SIGh(3)  = uicontrol('style','checkbox','background',colors(13,:),'foreground',colors(19,:),'pos',[10,63,200,18], 'Value',rem(VI_DEBUG,8)>=4,...
                              'str','Echo commands (hex)','call','sigstat(4,3)');
  SIGh(4) = uicontrol('style','checkbox','background',colors(13,:),'foreground',colors(19,:),'pos',[10,39,200,18],'Value',rem(VI_DEBUG,16)>=8,...
                              'str','Echo status','call','sigstat(4,4)');
  SIGh(5) = uicontrol('style','checkbox','background',colors(13,:),'foreground',colors(19,:),'pos',[10,135,200,18],'Value',aboEna, ...
                              'str','Enable aborts','call','sigstat(5)'); 
  SIGh(6) = uicontrol('style','checkbox','background',colors(13,:),'foreground',colors(19,:),'pos',[10,111,200,18],'Value',1, ...
                              'str','Enable status requests','call','sigstat(6)'); 
  [ASPIdos ASPImgr SCSIcard] = siglab('debug',-26);
  if (nc > 0)
    [HAid NumAdaptors] = siglab('debug',-45);
    if NumAdaptors > 1,
      SCSIcard=[SCSIcard,' (',int2str(HAid+1),' of ',int2str(NumAdaptors),')'];
    end;
  end;
  [ok DLLdate DLLver] = siglab('debug',-27);
  DLLnu = 0;  % set this to 1 if Queuing DLL is loaded
  DLLnum = 0;
  isQT = 0;
  if ok
        DLLnum = str2num(DLLver);
        if (DLLnum >= 3.26) DLLnu = 1; end;
        DLLver = [DLLver ' ' DLLdate];
  else DLLver = '??'; end;

  SIGnb = 0;            % assume no SigLabs found
  ilast = 0;            % last input channel number found
  olast = 0;            % last output channel number found
  if nc > 0 & ~isempty(ASPImgr)
    if DLLnu == 1,
       [junk isQT]  = siglab('debug',-6,-1); % dummy inquiry
    else
       siglab('debug',-6,-1); % dummy inquiry
    end;
    [CodeOk CodeDate CodeVer] = siglab('debug',-25,-1);
    %if CodeOk CodeVer=[CodeVer ' ' CodeDate]; else CodeVer='??'; end;
    if CodeOk CodeVer=[CodeVer ' ']; else CodeVer='??'; end;
    for id = 0:6    % locate all SigLabs on SCSI port
      [serial,in,out] = siglab('debug',-20,id);
      if in
        SIGnb = SIGnb+1;
        SIGb(SIGnb,1) = id;
        SIGb(SIGnb,3) = serial;
        SIGb(SIGnb,8) = ilast+1;
        SIGb(SIGnb,10) = olast+1;
        ilast = ilast + in;  olast = olast + out;
        SIGb(SIGnb,9) = ilast;
        SIGb(SIGnb,11) = olast;
        SIGb(SIGnb,12) = siglab('debug',-58,id);
        [SIGb(SIGnb,6) SIGb(SIGnb,7)] = siglab('debug',-37,id);
        if (SIGb(SIGnb,7)== 60),
                siglab('misc',-42);
                disp('Bogus Sram value');
                siglab('misc',-42);
                siglab('debug',-5,SIGnb-1);
                siglab('misc',-42);
                lupcnt = 0;
                while (SIGb(SIGnb,7)== 60) & (lupcnt < 10000),
                    siglab('misc',-42);
                    [SIGb(SIGnb,6) SIGb(SIGnb,7)] = siglab('debug',-37,id);
                    lupcnt = lupcnt+1;
                end;
                [serial,in,out] = siglab('debug',-20,id);
                SIGb(SIGnb,3) = serial;
                [CodeOk CodeDate CodeVer] = siglab('debug',-25,-1);
                if CodeOk CodeVer=[CodeVer ' ' CodeDate]; else CodeVer='??'; end;
        end;
        [ok date PROMver SIGb(SIGnb,2)] = siglab('debug',-24,id);
        if ok PROMver = str2num(PROMver(1:4)); else PROMver = -1; end;
        SIGb(SIGnb,4) = PROMver;
        SIGb(SIGnb,5) = (ok | CodeOk) & ~serial;
      end;
    end;
  end;  
  
  if isQT==1 ASPIdos_ =    ' Tagged Queuing in Use';
  else ASPIdos_ = ' ';
  end;
  
  SIGh(11) = uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,266,200,17]);
  SIGh(10) = uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,249,200,17]);
  SIGh(9) = uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,232,200,17]);
  SIGh(8) = uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,215,200,17]);
  SIGh(7)  = uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,198,200,17]);

  uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,349,200,17],'str',[' .DLL: V' DLLver]);
  if SIGnb
    uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,332,200,17],'str',[' .OUT: V' CodeVer]);
    uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,315,200,17],'str',sprintf(' %d In,  %d Out,  %dkHz BW',nc,outnc,bw/1000));
  end;
  uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,375,200,17], 'str',ASPIdos_);
  uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,392,200,17],  'str',[' Driver: ', ASPImgr]);
  uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[10,409,200,17],  'str',[' Card: ', SCSIcard]);
  uicontrol('style','text','background',colors(3,:),'foreground',colors(16,:),'horiz','left','pos',[5,6,95,17],  'str','SigLab status','background',colors(5,:),'foreground',colors(8,:),'horiz','center');
  % use sigstat(0,0) to enable the reload button
  if nargin>1 uicontrol('style','pushbutton','pos',[105,4,60,20],'str','Reload','call','sigstat(2)'); end;
  uicontrol('style','pushbutton','pos',[170,4,35,20],'str','Quit','call','sigstat(3)');
  if SIGnb
    s = '';
    for k=1:SIGnb
      s = [s sprintf(' SigLab %d of %d   (ID%d)|',k,SIGnb,SIGb(k,1))];
    end;
    s = s(1:length(s)-1);
  else s = ' No SigLabs found';
  end;
  SIGh(12) = uicontrol('style','popup','background',colors(13,:),'foreground',colors(19,:),'pos',[25,287,170,20],'str',s,'call','sigstat(1)');
  if SIGnb sigstat(1); end;
  eval('uifont(SaveFont);');

elseif Action == 1
  bx = get(SIGh(12),'value');
  mdlSTR = ['20-22 ';'20-42 ';'20-22A';'50-21 '];   % mdl = 0,1,2,3
  eeSTR = ['ok ';'BAD'];
  ndSTR = ['     ';'NoDis'];
  set(SIGh(11),'str',[' SigLab model:    ' mdlSTR(SIGb(bx,2)+1,:)]);
  set(SIGh(10),'str',[' SigLab serial #:  ' int2str(SIGb(bx,3))]);
  set(SIGh(9),'str',sprintf(' Prom: %4.2f    EErom: %s %s',...
            SIGb(bx,4),eeSTR(SIGb(bx,5)+1,:),...
        ndSTR(SIGb(bx,12)+1,:)));
  set(SIGh(8),'str',sprintf(' Dram: %dMb    Sram: %3.2fMb',...
            round(SIGb(bx,6)/256),SIGb(bx,7)/256));
  set(SIGh(7),'str',sprintf(' Inputs: %d-%d    Outputs: %d-%d',...
       SIGb(bx,8), SIGb(bx,9), SIGb(bx,10), SIGb(bx,11)));

elseif Action == 4
  k = 2^(In1-1); % k indicates VI_DEBUG bit to modify
  if rem(VI_DEBUG,2*k)>=k  VI_DEBUG=VI_DEBUG-k; end; % clear bit first
  v = get(SIGh(In1),'Value');   % get checkbox value
  if In1==1 v = 1-v; end;       % SCSI bit has opposite sense
  VI_DEBUG = VI_DEBUG + k * v;  % set bit if appropriate
  if SIGnb siglab('Debug',VI_DEBUG); end;

elseif Action == 5  siglab('Debug',get(SIGh(5),'Value')-30);

elseif Action == 6  if get(SIGh(6),'Value') k=-89; else k=-99; end;
                           siglab('Debug',k);

elseif Action == 2   % Reboot the SigLab and exit
  [stat owner] = hw_stat('owners');
  if isempty(owner)  % this doesn't quite work, but who cares! 
     siglab('debug',-3);  % kill status request
     pause(1);  siglab('rawcommand',768);  pause(1);
     close(SIGh(13));
  else tmsg('cannot reload while a vi is active!');
  end;

elseif Action == 3  close(SIGh(13)); % Quit button: Exit sigstat
end; % Main IF statement

% end function sigstat
