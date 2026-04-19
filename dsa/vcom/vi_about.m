  function Out1= vi_about(Action,In1,Owner,Version)
% function Out1= vi_about(Action,In1,Owner,Version) 
% About Modal Dialog, devolved from sigstat.mi
% reports system configuration information
% can be invoked with no input args to report hardware and support software info
%  e.g.  >> vi_about
%            or
% from a vi about menu pick callback in which case the Owner and Version strings 
% should be supplied e.g
%      %define
%          VI_VERSIONc='''v1.2xx''';
%      %end_define
%      .....
%      .....
%      'Callback',['vi_about(''init'',gcf,''vfg'',',VI_VERSIONc,')'],...
 
  %define
    %HTXTc = 17;
    %DYc   = 20;
    %DXc   = 10;
    %DYYc  = 95;
    %WLABc = 194;
    %TTOPc = 275;
    
    %f1    = 1;
    %sys   = 2;
    %pu    = 3;
  
    %GET_SN_IOc  =-20;
    %GET_ASPIc   =-26; 
    %GET_PROMc   =-24;
    %GET_RAMc    =-37;
    %GET_CODEc   =-25;
    
    %NLc           =  setstr(13);    % CR is newline 
    %TABc          =  setstr(9);     % tab character 
  %end_define
  global HAB_
  global VI_DEBUG

  if nargin==0
     Action  = 'init';
     In1     = [];
     Owner   = '';
     Version = [];
  end

  if strcmp(Action,'init')
     %In1 has current figure handle
      VIfont = 0;  % Use default font.
      if exist('vi_color.mat') ==2
         load vi_color 
         colors=stored_vi_colors;
      else
         colors=[[0,0,.25098];[0,0.50196,0.50196];[0.75294,0.75294,0.75294];[1,1,0];[0,0,0];[0,0,1];[0,1,0];[0,1,1];[1,1,0];[1,0,0];[0.25098,0,0];[0,1,0];[0.75294,0.75294,0.75294];[1,1,1];[1,1,1];[0,0,0];[0,0,0];[0,0,0];[0,0,0];[1,1,0];[1,0,0];[.3,.3,.3];[0.5, 1, 0];];
      end
     
      dialogname=['About ',Owner];
      
      if ~isempty(In1)
         pos=get(In1,'position');
      else
         pos=[30,30];
      end
       
      hf=figure('numbertitle','off','resize','off','menu','none',...
                'pos',[pos(1:2)+[0,20],194+18,400],... 
                'color',colors(1,:),... 
                'name',dialogname); 
      ss = ['''',dialogname,''''];           % note the quotes
      
      if beyondv4          
          set(hf,'WindowStyle','modal');
          s        = 'close(gcf)';           % normal  close
          SaveFont = eval('uifont(VIfont)'); 
      else
          modal(dialogname);   % set the figure to be modal ASAP  
          s  = [' modal(',ss,'); close(gcf);'];  % modal close
      end
      
      figure(hf);          % just in case
  
      uicontrol('str','Close About','pos',[10,2,90,20],...
                      'callback',s);
                      
      if ~isempty(Version)                
         uicontrol('style','text','position',[10,275+17,194,5*17],...
                   'BackgroundColor',colors(12,:),'ForegroundColor',colors(18,:),...
                   'HorizontalAlignment','left',...
                   'max',5,...
                   'enable','on',...
                   'string',[' ',Owner,': ',Version,setstr(13),' Copyright (c) 1999',setstr(13),' MTS Systems Inc.',setstr(13),' Fremont, CA 94539',setstr(13),' Tel: (510) 657-7555']);  
      end
  
      if isempty(VI_DEBUG) VI_DEBUG = 0;  end;
      [drv,path_n] = pathfind('vbin'); 
      % now load the SigLabs if they are not alreadly loaded
      [BUGnc,outnc,bw,~] = siglab('IOinit',[drv,path_n,'\siglab.out'],VI_DEBUG);
%       if BUGnc == 0 
%          disp('No SigLabs found; SCSI disabled');
%          VI_DEBUG = VI_DEBUG + 1 - rem(VI_DEBUG,2);
%          if VI_DEBUG == 1 
%             VI_DEBUG = 3;
%          end
%       end
      
      % locate all SigLabs on SCSI port
      targets=[];
      for itarget= 0:6
          [serial,in,out,modelcode] = siglab('debug',-20,itarget);
          % adr_sn_in=[itarget,serial,in]    
          if in ~=0
             targets = [targets,itarget]; 
          end
      end

     [ASPIdos ASPImgr SCSIcard]   = siglab('debug',-26);
      
     if ~isempty(ASPImgr)
        s=' SigLab Module #1';
        lt=length(targets);
        if lt > 1
           for i=2:lt
               s=[s,'| SigLab Module #',int2str(i)];
           end
        end
        HAB_(3)=uicontrol('style','popup','position',[10-1,275-95+17+5,194+2,17],...
                           'BackgroundColor',colors(13,:),'ForegroundColor',colors(19,:),...
                           'HorizontalAlignment','left',...
                           'value',1,...
                           'string',s,'userdata',targets,...
                           'CallBack','vi_about(''select_box'')');
        if ASPIdos==1
           ASPIdos_ = ' WinASPI NOT Installed';
        else
           ASPIdos_ = ' WinASPI Installed';
        end;
     
        uicontrol('style','text','position',[10,275-2*20,194,17],...
                  'BackgroundColor',colors(3,:),'ForegroundColor',colors(16,:),...
                  'HorizontalAlignment','left',...
                  'string',sprintf(' %d In; %d Out; %dkHz BW',BUGnc,outnc,bw/1000));
     else
        ASPIdos_ = ' ';
     end
   
     
      if BUGnc == 0 || isempty(ASPImgr)
          info_str='';
      else
         info_str=vi_about('get_config');
      end
                
      [boot_ok dll_date dll_ver] = siglab('debug',-27);
      if boot_ok
         uicontrol('style','text','position',[10,275-20,194,17],...
                   'BackgroundColor',colors(3,:),'ForegroundColor',colors(16,:),...
                   'HorizontalAlignment','left',...
                   'string',[' siglab.dll: V',dll_ver,' ',dll_date(1:min(9,length(dll_date)))]);
      end;
                
     if ~isempty(ASPImgr)
         HAB_(2)  = uicontrol('style','text','position',[10,275-95-4*17,194,5*17],...
                               'BackgroundColor',colors(3,:),'ForegroundColor',colors(16,:),...
                               'max',5,...
                               'enable','on',...
                               'HorizontalAlignment','left',...
                               'string',info_str);                       
     end
 
     uicontrol('style','text','position',[10,275-10*20,194,17],...
               'BackgroundColor',colors(3,:),'ForegroundColor',colors(16,:),...
               'HorizontalAlignment','left',...
               'string',ASPIdos_); 
                   
     uicontrol('style','text','position',[10,275-11*20,194,17],...
               'BackgroundColor',colors(3,:),'ForegroundColor',colors(16,:),...
               'HorizontalAlignment','left',...
               'string',[' Driver: ', ASPImgr]);                
     
     uicontrol('style','text','position',[10,275-12*20,194,17],...
               'BackgroundColor',colors(3,:),'ForegroundColor',colors(16,:),...
               'HorizontalAlignment','left',...
               'string',[' Card: ', SCSIcard]);
  
     if beyondv4
         SaveFont = eval('uifont(VIfont)');
     end
          
  elseif strcmp(Action,'get_config')
      
      targets = get(HAB_(3),'userdata');
      target  = targets(get(HAB_(3),'value'));
  
      [DramKw SramKw] = siglab('debug',-37,target);
      % gls mod for truncated inquiry string 10/5/99
      if (SramKw == 60), % this bogus value flags short inquiry string
                siglab('misc',-42); % check for pending status
                siglab('misc',-42); % again
                siglab('debug',-5,target,1); %send get inquiry data
                siglab('misc',-42);
                lupcnt = 0;
                while (SramKw == 60) && (lupcnt < 10000),
                    siglab('misc',-42);
                    [DramKw SramKw] = siglab('debug',-37,target);
                    lupcnt = lupcnt+1;
                end
      end
      % end mod for truncated inquiry string 10/5/99
      [serial,in,out,modelcode]  = siglab('debug',-20,target);
      if serial == 65535  Out1 = ' Disconnect NOT ENABLED ';
      
      elseif in==0 
          s1 = [' No SigLabs Found!'];
      else
        % 21-Apr-1998 16:14 GLS - added 50-21 text here 
        mdlSTR = ['20-22 ';'20-42 ';'20-22a';'50-21 ';'??-?? '];   % mdl = 0,1,2,3
          if (modelcode < 0) | (modelcode > 4)
           modelcode = 4;
          end;
          s1 = [' SigLab ',mdlSTR(modelcode+1,:),' SN:',int2str(serial)];
      end

      [boot_ok boot_date boot_ver] = siglab('debug',-24,target);
      if ~boot_ok  
          boot_date = ' ';
          boot_ver = '??';
      end
 
      s2 = [' Boot Prom:  V',boot_ver,'    ID: ',int2str(target)];
      
      s3 = sprintf(' Dram: %dMb   Sram: %3.2fMb',DramKw/256,SramKw/256);
     
      [code_ok code_date code_ver] = siglab('debug',-25,target);
      if ~code_ok  
          code_date = ' ';
          code_ver = '??';
      end
      s4=[' Siglab.out: V',code_ver];
      for i=1:find(target==targets)
          if i==1 
             firstin  =1;
             firstout =1;
          else
             firstin = firstin+in;
             firstout= firstout+out;
          end;
          [serial,in, out] = siglab('debug',-20,targets(i));
     end; 
     s5 = [' Inputs: ',int2str(firstin),'-',int2str(firstin+in-1),...
             '     Outputs: ',int2str(firstout),'-',int2str(firstout+out-1)];
     Out1=[s1,setstr(13),s2,setstr(13),s3,setstr(13),s4,setstr(13),s5];
     
  elseif strcmp(Action,'select_box')
     set(HAB_(2),'string',vi_about('get_config'));
     
  else
     disp([Action,' not recognized in vi_about.m']);
  end
%end function vi_about
